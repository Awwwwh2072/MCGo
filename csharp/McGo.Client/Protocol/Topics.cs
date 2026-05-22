namespace McGo.Client.Protocol;

public static class Topics
{
    public const string TopicServerAnnounce = "mcgo/v1/server/announce";
    public const string TopicServerTree = "mcgo/v1/server/tree";
    public const string TopicServerChallengeWild = "mcgo/v1/server/challenge/+";
    public const string TopicServerAuthResultWild = "mcgo/v1/server/auth_result/+";
    public const string TopicServerFileMetaWild = "mcgo/v1/server/file/+/meta";
    public const string TopicServerFileChunkWild = "mcgo/v1/server/file/+/chunk/+";
    public const string TopicServerFileDoneWild = "mcgo/v1/server/file/+/done";
    public const string TopicServerFileAbortWild = "mcgo/v1/server/file/+/abort";

    public static string ServerChallengeTopic(string clientId) =>
        $"mcgo/v1/server/challenge/{clientId}";

    public static string ServerAuthResultTopic(string clientId) =>
        $"mcgo/v1/server/auth_result/{clientId}";

    public static string ClientHelloTopic(string clientId) =>
        $"mcgo/v1/client/{clientId}/hello";

    public static string ClientAuthResponseTopic(string clientId) =>
        $"mcgo/v1/client/{clientId}/auth_response";

    public static string ClientFileRequestTopic(string clientId) =>
        $"mcgo/v1/client/{clientId}/file_request";

    public static string ClientStatusTopic(string clientId) =>
        $"mcgo/v1/client/{clientId}/status";

    public static string? ExtractFileId(string topic)
    {
        var parts = topic.Split('/');
        if (parts.Length >= 5 && parts[0] == "mcgo" && parts[1] == "v1" && parts[2] == "server" && parts[3] == "file")
            return parts[4];
        return null;
    }

    public static int? ExtractChunkSeq(string topic)
    {
        var parts = topic.Split('/');
        if (parts.Length >= 7 && parts[5] == "chunk" && int.TryParse(parts[6], out var seq))
            return seq;
        return null;
    }
}
