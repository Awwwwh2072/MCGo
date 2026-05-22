namespace McGo.Client.Sync;

/// <summary>Maps remote tree paths to local sync paths (server <c>clientmods/</c> → client <c>mods/</c>).</summary>
public static class PathMapping
{
    public const string RemoteClientModsPrefix = "clientmods/";
    public const string LocalModsPrefix = "mods/";

    public static string MapRemoteToLocal(string remotePath)
    {
        if (remotePath.StartsWith(RemoteClientModsPrefix, StringComparison.Ordinal))
            return LocalModsPrefix + remotePath[RemoteClientModsPrefix.Length..];
        return remotePath;
    }
}
