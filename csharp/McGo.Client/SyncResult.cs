namespace McGo.Client;

/// <summary>Result of a sync operation – aligned with Python <c>SyncResult.to_dict()</c>.</summary>
public sealed class SyncResult
{
    public bool Success { get; set; }
    public List<string> FilesDownloaded { get; } = new();
    public List<string> FilesFailed { get; } = new();
    public List<string> Errors { get; } = new();
}
