using System.Security.Cryptography;

namespace McGo.Client.Sync;

public sealed record FileTreeScanResult(
    int Version,
    double Timestamp,
    string BasePath,
    IReadOnlyDictionary<string, FileEntryInfo> Files,
    IReadOnlyList<string> Directories);

public sealed record FileEntryInfo(
    long Size,
    double Mtime,
    string Sha256,
    bool IsBinary,
    bool ShouldCompress,
    string? Error = null);

public sealed record FetchEntry(string ServerPath, string LocalPath, string Reason);

public static class FileTree
{
    private static readonly HashSet<string> CompressedExtensions = new(StringComparer.OrdinalIgnoreCase)
    {
        ".zip", ".jar", ".war", ".ear", ".gz", ".bz2", ".xz", ".7z", ".rar",
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".webm",
        ".mp4", ".mp3", ".ogg", ".flac", ".avi", ".mkv",
        ".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods",
        ".tgz", ".tar.gz", ".tar.bz2", ".tar.xz",
    };

    public static FileTreeScanResult Scan(string basePath, IgnoreRules? ignoreRules)
    {
        var baseFull = Path.GetFullPath(basePath);
        Directory.CreateDirectory(baseFull);
        var files = new Dictionary<string, FileEntryInfo>(StringComparer.Ordinal);
        var directories = new List<string>();

        void VisitDirectory(string dirFull, string relDirNorm)
        {
            if (!string.IsNullOrEmpty(relDirNorm) && ignoreRules?.IsIgnored(relDirNorm, isDir: true) == true)
                return;

            if (!string.IsNullOrEmpty(relDirNorm))
                directories.Add(relDirNorm);

            string[] subdirs;
            try
            {
                subdirs = Directory.GetDirectories(dirFull);
            }
            catch
            {
                return;
            }

            foreach (var sub in subdirs.OrderBy(s => s, StringComparer.Ordinal))
            {
                var name = Path.GetFileName(sub);
                var childRel = string.IsNullOrEmpty(relDirNorm)
                    ? name
                    : $"{relDirNorm}/{name}";
                childRel = NormalizeRelPath(childRel);
                VisitDirectory(sub, childRel);
            }

            string[] fnames;
            try
            {
                fnames = Directory.GetFiles(dirFull).Select(Path.GetFileName).Where(n => n != null).Cast<string>().OrderBy(s => s, StringComparer.Ordinal).ToArray();
            }
            catch
            {
                return;
            }

            foreach (var fname in fnames)
            {
                var fileRel = string.IsNullOrEmpty(relDirNorm)
                    ? fname
                    : $"{relDirNorm}/{fname}";
                fileRel = NormalizeRelPath(fileRel);
                if (ignoreRules?.IsIgnored(fileRel, isDir: false) == true)
                    continue;

                var filePath = Path.Combine(dirFull, fname);
                try
                {
                    var fi = new FileInfo(filePath);
                    var sha = HashFile(filePath);
                    var isBinary = DetectBinary(filePath);
                    var ext = Path.GetExtension(fname).ToLowerInvariant();
                    var shouldCompress = !CompressedExtensions.Contains(ext) && !IsBinaryFallback(filePath);

                    files[fileRel] = new FileEntryInfo(
                        fi.Length,
                        (double)new DateTimeOffset(fi.LastWriteTimeUtc).ToUnixTimeSeconds(),
                        sha,
                        isBinary,
                        shouldCompress);
                }
                catch (Exception ex)
                {
                    files[fileRel] = new FileEntryInfo(0, 0, "", false, false, ex.Message);
                }
            }
        }

        VisitDirectory(baseFull, "");

        return new FileTreeScanResult(
            1,
            (double)DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
            baseFull,
            files.OrderBy(kv => kv.Key).ToDictionary(kv => kv.Key, kv => kv.Value, StringComparer.Ordinal),
            directories.Distinct(StringComparer.Ordinal).OrderBy(s => s, StringComparer.Ordinal).ToList());
    }

    public static List<FetchEntry> Diff(
        FileTreeScanResult local,
        IReadOnlyDictionary<string, object?> remoteTree,
        IgnoreRules? ignoreRules = null)
    {
        var toFetch = new List<FetchEntry>();
        if (!remoteTree.TryGetValue("files", out var filesObj) || filesObj is not Dictionary<string, object?> remoteFiles)
            return toFetch;

        var localFiles = local.Files;

        foreach (var (remotePath, remoteInfoObj) in remoteFiles)
        {
            var localPath = PathMapping.MapRemoteToLocal(remotePath);
            if (ignoreRules?.IsIgnored(localPath, isDir: false) == true)
                continue;
            var remoteSha = GetSha256(remoteInfoObj);
            if (!localFiles.TryGetValue(localPath, out var localInfo))
            {
                toFetch.Add(new FetchEntry(remotePath, localPath, "missing"));
                continue;
            }

            if (!string.Equals(localInfo.Sha256, remoteSha, StringComparison.Ordinal))
                toFetch.Add(new FetchEntry(remotePath, localPath, "changed"));
        }

        return toFetch;
    }

    private static string GetSha256(object? remoteInfoObj)
    {
        if (remoteInfoObj is not Dictionary<string, object?> d)
            return "";
        if (!d.TryGetValue("sha256", out var v) || v is null)
            return "";
        return v switch
        {
            string s => s,
            _ => v.ToString() ?? "",
        };
    }

    private static string NormalizeRelPath(string p) => p.Replace('\\', '/');

    private static string HashFile(string filepath)
    {
        using var sha = SHA256.Create();
        using var fs = File.OpenRead(filepath);
        return Convert.ToHexString(sha.ComputeHash(fs)).ToLowerInvariant();
    }

    private static bool DetectBinary(string filepath)
    {
        try
        {
            using var fs = File.OpenRead(filepath);
            var head = new byte[512];
            var n = fs.Read(head, 0, head.Length);
            for (var i = 0; i < n; i++)
            {
                if (head[i] == 0)
                    return true;
            }

            return false;
        }
        catch
        {
            return false;
        }
    }

    private static bool IsBinaryFallback(string filepath) =>
        CompressedExtensions.Contains(Path.GetExtension(filepath).ToLowerInvariant());
}
