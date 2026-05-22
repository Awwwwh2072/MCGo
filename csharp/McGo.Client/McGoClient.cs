using System.Collections.Concurrent;
using System.Text;
using McGo.Client.Auth;
using McGo.Client.Config;
using McGo.Client.Crypto;
using McGo.Client.Protocol;
using McGo.Client.Sync;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;
using MQTTnet;
using MQTTnet.Client;
using MQTTnet.Protocol;

namespace McGo.Client;

/// <summary>MQTT file sync client (protocol-compatible with Python <c>mcgo.client.McGoClient</c>).</summary>
public sealed class McGoClient : IAsyncDisposable
{
    private readonly ClientConfig _cfg;
    private readonly string _configDir;
    private readonly byte[] _encryptionKey;
    private readonly ClientAuth _auth;
    private readonly ILogger _log;
    private readonly IgnoreRules _ignore;

    private IMqttClient? _mqtt;
    private volatile bool _authenticated;
    private volatile bool _running;
    private CancellationTokenSource? _watchCts;

    public McGoClient(string configPath, ILogger? logger = null)
    {
        _cfg = ConfigLoader.Load(configPath);
        _configDir = Path.GetDirectoryName(Path.GetFullPath(configPath)) ?? ".";
        _encryptionKey = Convert.FromBase64String(_cfg.EncryptionKey.Trim());
        _auth = new ClientAuth(_cfg.ClientPrivateKey);
        _log = logger ?? NullLogger.Instance;
        var ignorePath = Path.Combine(_configDir, _cfg.IgnoreFile);
        _ignore = new IgnoreRules(ignorePath, _cfg.SyncDirectory);
    }

    public async Task<SyncResult> SyncAsync(CancellationToken cancellationToken = default, TimeSpan? timeout = null)
    {
        var result = new SyncResult();
        Directory.CreateDirectory(_cfg.SyncDirectory);

        using var linked = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        linked.CancelAfter(timeout ?? TimeSpan.FromSeconds(60));

        var syncDone = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var transfers = new ConcurrentDictionary<string, TransferState>(StringComparer.Ordinal);

        var factory = new MqttFactory();
        var mqtt = factory.CreateMqttClient();

        void TrySignalSyncComplete()
        {
            if (transfers.IsEmpty)
                syncDone.TrySetResult();
        }

        mqtt.ConnectedAsync += async _ =>
        {
            try
            {
                await mqtt.SubscribeAsync(
                    new MqttClientSubscribeOptions
                    {
                        TopicFilters =
                        [
                            new MqttTopicFilterBuilder().WithTopic(Topics.ServerChallengeTopic(_cfg.ClientId))
                                .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.ExactlyOnce).Build(),
                            new MqttTopicFilterBuilder().WithTopic(Topics.ServerAuthResultTopic(_cfg.ClientId))
                                .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.ExactlyOnce).Build(),
                        ],
                    },
                    linked.Token);

                var hello = MessageSerializer.SerializeMessage(new { client_id = _cfg.ClientId });
                await mqtt.PublishAsync(
                    new MqttApplicationMessageBuilder()
                        .WithTopic(Topics.ClientHelloTopic(_cfg.ClientId))
                        .WithPayload(hello)
                        .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.ExactlyOnce)
                        .Build(),
                    linked.Token);
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "MQTT subscribe/hello failed");
                result.Errors.Add(ex.Message);
                syncDone.TrySetResult();
            }
        };

        mqtt.DisconnectedAsync += _ =>
        {
            _log.LogWarning("Disconnected from MQTT broker");
            syncDone.TrySetResult();
            return Task.CompletedTask;
        };

        mqtt.ApplicationMessageReceivedAsync += async e =>
        {
            try
            {
                var payload = e.ApplicationMessage.PayloadSegment.ToArray();
                await DispatchMessageAsync(e.ApplicationMessage.Topic, payload, mqtt, result, transfers, syncDone,
                    TrySignalSyncComplete, linked.Token).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "Error handling message on {Topic}", e.ApplicationMessage.Topic);
            }
        };

        try
        {
            var opts = new MqttClientOptionsBuilder()
                .WithTcpServer(_cfg.MqttHost, _cfg.MqttPort)
                .WithClientId($"{_cfg.ClientId}-{Environment.ProcessId}")
                .WithCleanSession()
                .Build();

            await mqtt.ConnectAsync(opts, linked.Token).ConfigureAwait(false);
            await syncDone.Task.WaitAsync(linked.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            result.Errors.Add("Sync timed out");
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Sync failed");
            result.Errors.Add(ex.Message);
        }
        finally
        {
            if (mqtt.IsConnected)
            {
                try
                {
                    await mqtt.DisconnectAsync().ConfigureAwait(false);
                }
                catch
                {
                    /* ignore */
                }
            }

            mqtt.Dispose();
        }

        result.Success = result.FilesDownloaded.Count > 0 ||
                         (result.FilesFailed.Count == 0 && result.Errors.Count == 0);
        return result;
    }

    public async Task StartWatchAsync(CancellationToken cancellationToken = default)
    {
        Directory.CreateDirectory(_cfg.SyncDirectory);
        _watchCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        var ct = _watchCts.Token;

        var factory = new MqttFactory();
        _mqtt = factory.CreateMqttClient();
        var transfers = new ConcurrentDictionary<string, TransferState>(StringComparer.Ordinal);
        var syncResult = new SyncResult();

        void TrySignalNop()
        {
            /* watch: completion only when tree says up-to-date (handled inside HandleTree) */
        }

        _mqtt.ConnectedAsync += async _ =>
        {
            try
            {
                await _mqtt!.SubscribeAsync(
                    new MqttClientSubscribeOptions
                    {
                        TopicFilters =
                        [
                            new MqttTopicFilterBuilder().WithTopic(Topics.ServerChallengeTopic(_cfg.ClientId))
                                .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.ExactlyOnce).Build(),
                            new MqttTopicFilterBuilder().WithTopic(Topics.ServerAuthResultTopic(_cfg.ClientId))
                                .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.ExactlyOnce).Build(),
                        ],
                    },
                    ct);

                var hello = MessageSerializer.SerializeMessage(new { client_id = _cfg.ClientId });
                await _mqtt.PublishAsync(
                    new MqttApplicationMessageBuilder()
                        .WithTopic(Topics.ClientHelloTopic(_cfg.ClientId))
                        .WithPayload(hello)
                        .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.ExactlyOnce)
                        .Build(),
                    ct);
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "Watch: subscribe/hello failed");
            }
        };

        _mqtt.ApplicationMessageReceivedAsync += async e =>
        {
            try
            {
                if (_mqtt is null)
                    return;
                var payload = e.ApplicationMessage.PayloadSegment.ToArray();
                await DispatchMessageAsync(e.ApplicationMessage.Topic, payload, _mqtt, syncResult, transfers, null,
                    TrySignalNop, ct).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "Watch: message handler error");
            }
        };

        var opts = new MqttClientOptionsBuilder()
            .WithTcpServer(_cfg.MqttHost, _cfg.MqttPort)
            .WithClientId($"{_cfg.ClientId}-{Environment.ProcessId}")
            .WithCleanSession()
            .Build();

        await _mqtt.ConnectAsync(opts, ct).ConfigureAwait(false);
        _running = true;
        _log.LogInformation("McGo client watch mode running");

        try
        {
            while (!ct.IsCancellationRequested && _running)
            {
                await Task.Delay(1000, ct).ConfigureAwait(false);
                if (_authenticated && _mqtt.IsConnected)
                {
                    var status = MessageSerializer.SerializeMessage(new
                    {
                        client_id = _cfg.ClientId,
                        display_name = _cfg.DisplayName,
                        timestamp = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
                        authenticated = _authenticated,
                    });
                    await _mqtt.PublishAsync(
                        new MqttApplicationMessageBuilder()
                            .WithTopic(Topics.ClientStatusTopic(_cfg.ClientId))
                            .WithPayload(status)
                            .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtMostOnce)
                            .Build(),
                        ct).ConfigureAwait(false);
                }
            }
        }
        catch (OperationCanceledException)
        {
            /* normal shutdown */
        }
    }

    public void Stop()
    {
        _running = false;
        _watchCts?.Cancel();
    }

    public async ValueTask DisposeAsync()
    {
        Stop();
        if (_mqtt is not null)
        {
            try
            {
                if (_mqtt.IsConnected)
                    await _mqtt.DisconnectAsync().ConfigureAwait(false);
            }
            catch
            {
                /* ignore */
            }

            _mqtt.Dispose();
            _mqtt = null;
        }

        _watchCts?.Dispose();
        _watchCts = null;
    }

    private async Task DispatchMessageAsync(
        string topic,
        byte[] payload,
        IMqttClient mqtt,
        SyncResult result,
        ConcurrentDictionary<string, TransferState> transfers,
        TaskCompletionSource? syncDone,
        Action trySignalSyncComplete,
        CancellationToken ct)
    {
        var parts = topic.Split('/');
        if (parts.Length < 4 || parts[0] != "mcgo" || parts[1] != "v1" || parts[2] != "server")
            return;

        if (topic == Topics.TopicServerTree)
        {
            var data = MessageSerializer.DeserializeMessage(payload);
            await HandleTreeAsync(data, mqtt, result, transfers, syncDone, trySignalSyncComplete, ct)
                .ConfigureAwait(false);
        }
        else if (topic == Topics.TopicServerAnnounce)
        {
            var data = MessageSerializer.DeserializeMessage(payload);
            _log.LogInformation("Server announce: {Data}", data);
        }
        else if (topic.EndsWith("/done", StringComparison.Ordinal))
        {
            var fileId = Topics.ExtractFileId(topic);
            if (fileId is not null)
                HandleFileDone(fileId, result, transfers, trySignalSyncComplete);
        }
        else if (topic.EndsWith("/abort", StringComparison.Ordinal))
        {
            var fileId = Topics.ExtractFileId(topic);
            if (fileId is not null)
                HandleFileAbort(fileId, result, transfers, trySignalSyncComplete);
        }
        else if (topic.Contains("/chunk/", StringComparison.Ordinal))
        {
            var fileId = Topics.ExtractFileId(topic);
            var seq = Topics.ExtractChunkSeq(topic);
            if (fileId is not null && seq is not null)
                HandleFileChunk(fileId, seq.Value, payload, transfers);
        }
        else if (topic.EndsWith("/meta", StringComparison.Ordinal))
        {
            var fileId = Topics.ExtractFileId(topic);
            if (fileId is not null)
            {
                var data = MessageSerializer.DeserializeMessage(payload);
                HandleFileMeta(fileId, data, transfers);
            }
        }
        else if (topic.Contains("/challenge/", StringComparison.Ordinal))
        {
            var data = MessageSerializer.DeserializeMessage(payload);
            await HandleChallengeAsync(data, mqtt, ct).ConfigureAwait(false);
        }
        else if (topic.Contains("/auth_result/", StringComparison.Ordinal))
        {
            var data = MessageSerializer.DeserializeMessage(payload);
            await HandleAuthResultAsync(data, mqtt, result, syncDone).ConfigureAwait(false);
        }
    }

    private async Task HandleChallengeAsync(IReadOnlyDictionary<string, object?> payload, IMqttClient mqtt,
        CancellationToken ct)
    {
        var challenge = _auth.HandleChallenge(payload);
        if (challenge is null)
        {
            _log.LogError("Invalid challenge received");
            return;
        }

        var response = _auth.BuildResponse();
        await mqtt.PublishAsync(
            new MqttApplicationMessageBuilder()
                .WithTopic(Topics.ClientAuthResponseTopic(_cfg.ClientId))
                .WithPayload(MessageSerializer.SerializeMessage(response))
                .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.ExactlyOnce)
                .Build(),
            ct).ConfigureAwait(false);
    }

    private async Task HandleAuthResultAsync(
        IReadOnlyDictionary<string, object?> payload,
        IMqttClient mqtt,
        SyncResult result,
        TaskCompletionSource? syncDone)
    {
        var success = DictGetBool(payload, "success");
        var message = DictGetString(payload, "message");

        if (success)
        {
            _log.LogInformation("Authentication successful: {Message}", message);
            _authenticated = true;
            await mqtt.SubscribeAsync(
                new MqttClientSubscribeOptions
                {
                    TopicFilters =
                    [
                        new MqttTopicFilterBuilder().WithTopic(Topics.TopicServerTree)
                            .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtMostOnce).Build(),
                        new MqttTopicFilterBuilder().WithTopic(Topics.TopicServerFileMetaWild)
                            .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce).Build(),
                        new MqttTopicFilterBuilder().WithTopic(Topics.TopicServerFileChunkWild)
                            .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce).Build(),
                        new MqttTopicFilterBuilder().WithTopic(Topics.TopicServerFileDoneWild)
                            .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce).Build(),
                        new MqttTopicFilterBuilder().WithTopic(Topics.TopicServerFileAbortWild)
                            .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce).Build(),
                    ],
                },
                CancellationToken.None).ConfigureAwait(false);
        }
        else
        {
            _log.LogError("Authentication failed: {Message}", message);
            result.Errors.Add($"Authentication failed: {message}");
            syncDone?.TrySetResult();
        }
    }

    private async Task HandleTreeAsync(
        IReadOnlyDictionary<string, object?> payload,
        IMqttClient mqtt,
        SyncResult result,
        ConcurrentDictionary<string, TransferState> transfers,
        TaskCompletionSource? syncDone,
        Action trySignalSyncComplete,
        CancellationToken ct)
    {
        if (payload.Count == 0 || !payload.ContainsKey("files"))
            return;

        _log.LogInformation("Received file tree: {Count} files", CountRemoteFiles(payload));

        var local = FileTree.Scan(_cfg.SyncDirectory, _ignore);
        var toFetch = FileTree.Diff(local, payload);

        if (toFetch.Count == 0)
        {
            _log.LogInformation("All files up to date.");
            syncDone?.TrySetResult();
            return;
        }

        _log.LogInformation("Need to fetch {Count} files", toFetch.Count);
        foreach (var entry in toFetch)
            await RequestFileAsync(entry.ServerPath, entry.LocalPath, mqtt, transfers, ct).ConfigureAwait(false);
    }

    private static int CountRemoteFiles(IReadOnlyDictionary<string, object?> payload)
    {
        if (!payload.TryGetValue("files", out var f) || f is not Dictionary<string, object?> d)
            return 0;
        return d.Count;
    }

    private async Task RequestFileAsync(
        string serverPath,
        string localPath,
        IMqttClient mqtt,
        ConcurrentDictionary<string, TransferState> transfers,
        CancellationToken ct)
    {
        var fileId = MessageSerializer.NewFileId();
        var request = MessageSerializer.SerializeMessage(new { file_id = fileId, path = serverPath });

        transfers[fileId] = new TransferState
        {
            Meta = new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                ["server_path"] = serverPath,
                ["local_path"] = localPath,
                ["chunk_count"] = 0,
                ["compressed"] = false,
            },
        };

        await mqtt.PublishAsync(
            new MqttApplicationMessageBuilder()
                .WithTopic(Topics.ClientFileRequestTopic(_cfg.ClientId))
                .WithPayload(request)
                .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce)
                .Build(),
            ct).ConfigureAwait(false);
    }

    private void HandleFileMeta(string fileId, IReadOnlyDictionary<string, object?> payload,
        ConcurrentDictionary<string, TransferState> transfers)
    {
        if (!transfers.TryGetValue(fileId, out var state))
            return;

        lock (state.Gate)
        {
            var localPath = DictGetString(state.Meta, "local_path");
            var serverPath = DictGetString(state.Meta, "server_path");
            foreach (var kv in payload)
                state.Meta[kv.Key] = kv.Value;
            if (!string.IsNullOrEmpty(localPath))
                state.Meta["local_path"] = localPath;
            if (!string.IsNullOrEmpty(serverPath))
                state.Meta["server_path"] = serverPath;
        }
    }

    private void HandleFileChunk(string fileId, int seq, byte[] payload,
        ConcurrentDictionary<string, TransferState> transfers)
    {
        if (!transfers.TryGetValue(fileId, out var state))
            return;

        lock (state.Gate)
        {
            state.Chunks[seq] = payload;
            state.Received++;
        }
    }

    private void HandleFileDone(
        string fileId,
        SyncResult result,
        ConcurrentDictionary<string, TransferState> transfers,
        Action trySignalSyncComplete)
    {
        if (!transfers.TryRemove(fileId, out var state))
        {
            _log.LogWarning("Got done for unknown file_id: {FileId}", fileId);
            return;
        }

        Dictionary<int, byte[]> chunksCopy;
        Dictionary<string, object?> metaCopy;
        lock (state.Gate)
        {
            chunksCopy = new Dictionary<int, byte[]>(state.Chunks);
            metaCopy = new Dictionary<string, object?>(state.Meta, StringComparer.Ordinal);
        }

        var localPath = DictGetString(metaCopy, "local_path");
        if (string.IsNullOrEmpty(localPath))
            localPath = DictGetString(metaCopy, "path");

        var compressed = DictGetBool(metaCopy, "compressed");

        byte[] data;
        try
        {
            data = ChunkAssembler.ReassembleChunks(chunksCopy);
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Failed to reassemble chunks for {Path}", localPath);
            result.FilesFailed.Add(localPath);
            trySignalSyncComplete();
            return;
        }

        try
        {
            var aad = Encoding.UTF8.GetBytes(fileId);
            data = AesGcmHelper.Decrypt(_encryptionKey, data, aad);
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Failed to decrypt {Path}", localPath);
            result.FilesFailed.Add(localPath);
            trySignalSyncComplete();
            return;
        }

        if (compressed)
        {
            try
            {
                data = ZlibHelper.Decompress(data);
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "Failed to decompress {Path}", localPath);
                result.FilesFailed.Add(localPath);
                trySignalSyncComplete();
                return;
            }
        }

        var dest = Path.Combine(_cfg.SyncDirectory, localPath.Replace('/', Path.DirectorySeparatorChar));
        if (!IsUnderRoot(_cfg.SyncDirectory, dest))
        {
            _log.LogError("Path traversal blocked: {Path}", localPath);
            result.FilesFailed.Add(localPath);
            trySignalSyncComplete();
            return;
        }

        try
        {
            var dir = Path.GetDirectoryName(dest);
            if (!string.IsNullOrEmpty(dir))
                Directory.CreateDirectory(dir);
            File.WriteAllBytes(dest, data);
            _log.LogInformation("Downloaded: {Path} ({Len} bytes)", localPath, data.Length);
            result.FilesDownloaded.Add(localPath);
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Failed to write {Path}", localPath);
            result.FilesFailed.Add(localPath);
        }

        trySignalSyncComplete();
    }

    private void HandleFileAbort(
        string fileId,
        SyncResult result,
        ConcurrentDictionary<string, TransferState> transfers,
        Action trySignalSyncComplete)
    {
        if (!transfers.TryRemove(fileId, out var state))
            return;

        string localPath;
        lock (state.Gate)
        {
            localPath = DictGetString(state.Meta, "local_path");
            if (string.IsNullOrEmpty(localPath))
                localPath = DictGetString(state.Meta, "path");
        }

        _log.LogWarning("File transfer aborted: {Path}", localPath);
        result.FilesFailed.Add(localPath);
        trySignalSyncComplete();
    }

    private static bool IsUnderRoot(string root, string candidate)
    {
        var r = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar);
        var c = Path.GetFullPath(candidate);
        var prefix = r + Path.DirectorySeparatorChar;
        return c.Equals(r, StringComparison.OrdinalIgnoreCase) ||
               c.StartsWith(prefix, StringComparison.OrdinalIgnoreCase);
    }

    private static bool DictGetBool(IReadOnlyDictionary<string, object?> d, string key)
    {
        if (!d.TryGetValue(key, out var v) || v is null)
            return false;
        return v switch
        {
            bool b => b,
            long n => n != 0,
            int i => i != 0,
            _ => false,
        };
    }

    private static string DictGetString(IReadOnlyDictionary<string, object?> d, string key)
    {
        if (!d.TryGetValue(key, out var v) || v is null)
            return "";
        return v switch
        {
            string s => s,
            _ => v.ToString() ?? "",
        };
    }

    private sealed class TransferState
    {
        public readonly object Gate = new();
        public Dictionary<int, byte[]> Chunks { get; } = new();
        public int Received;
        public Dictionary<string, object?> Meta { get; init; } = new(StringComparer.Ordinal);
    }
}
