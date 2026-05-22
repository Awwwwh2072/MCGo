using System.Globalization;
using Tomlyn;
using Tomlyn.Model;
using Tomlyn.Syntax;

namespace McGo.Client.Config;

public static class ConfigLoader
{
    public static ClientConfig Load(string configPath)
    {
        var abs = Path.GetFullPath(configPath);
        if (!File.Exists(abs))
            throw new ConfigException($"Config file not found: {configPath}");

        var configDir = Path.GetDirectoryName(abs) ?? ".";
        var text = File.ReadAllText(abs);
        if (!Toml.TryToModel(text, out TomlTable? root, out var diags, abs, options: null) || root is null)
        {
            var msg = string.Join("; ", (diags ?? Enumerable.Empty<DiagnosticMessage>()).Select(d => d.ToString()));
            throw new ConfigException($"Invalid TOML: {msg}");
        }

        var clientRaw = AsTable(root, "client");
        var authRaw = AsTable(root, "auth");
        var loggingRaw = AsTable(root, "logging");

        var cfg = new ClientConfig
        {
            ClientId = GetString(clientRaw, "client_id", "default"),
            DisplayName = GetString(clientRaw, "display_name", "Default Client"),
            MqttHost = GetString(clientRaw, "mqtt_host", "localhost"),
            MqttPort = GetInt(clientRaw, "mqtt_port", 1883),
            SyncDirectory = ResolvePath(GetString(clientRaw, "sync_directory", "./sync"), configDir),
            IgnoreFile = GetString(clientRaw, "ignore_file", ".mcgoignore"),
            EncryptionKey = GetString(clientRaw, "encryption_key", ""),
            ClientPrivateKey = ResolvePath(GetString(authRaw, "client_private_key", "keys/client_private.pem"), configDir),
            LogLevel = GetString(loggingRaw, "level", "INFO"),
            LogFile = GetString(loggingRaw, "file", ""),
        };

        ValidatePort(cfg.MqttPort);
        ValidateEncryptionKey(cfg.EncryptionKey);
        return cfg;
    }

    private static TomlTable AsTable(TomlTable root, string key)
    {
        if (!root.TryGetValue(key, out var v) || v is not TomlTable t)
            return new TomlTable();
        return t;
    }

    private static string GetString(TomlTable t, string key, string def)
    {
        if (!t.TryGetValue(key, out var v) || v is null)
            return def;
        return v switch
        {
            string s => s,
            long n => n.ToString(CultureInfo.InvariantCulture),
            int i => i.ToString(CultureInfo.InvariantCulture),
            double d => d.ToString(CultureInfo.InvariantCulture),
            bool b => b ? "true" : "false",
            _ => def,
        };
    }

    private static int GetInt(TomlTable t, string key, int def)
    {
        if (!t.TryGetValue(key, out var v) || v is null)
            return def;
        return v switch
        {
            long n => (int)n,
            int i => i,
            double d => (int)d,
            string s => int.TryParse(s, NumberStyles.Integer, CultureInfo.InvariantCulture, out var x) ? x : def,
            _ => def,
        };
    }

    private static string ResolvePath(string path, string configDir)
    {
        if (Path.IsPathRooted(path))
            return path;
        return Path.GetFullPath(Path.Combine(configDir, path));
    }

    private static void ValidatePort(int port)
    {
        if (port is < 1 or > 65535)
            throw new ConfigException($"mqtt_port must be in range 1-65535 (got {port})");
    }

    private static void ValidateEncryptionKey(string key)
    {
        if (string.IsNullOrWhiteSpace(key))
            throw new ConfigException("encryption_key must not be empty");
        byte[] raw;
        try
        {
            raw = Convert.FromBase64String(key.Trim());
        }
        catch
        {
            throw new ConfigException("encryption_key must be valid base64");
        }

        if (raw.Length != 32)
            throw new ConfigException($"encryption_key must decode to 32 bytes (got {raw.Length})");
    }
}

public sealed class ConfigException : Exception
{
    public ConfigException(string message) : base(message) { }
}
