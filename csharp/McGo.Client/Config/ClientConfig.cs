namespace McGo.Client.Config;

public sealed class ClientConfig
{
    public string ClientId { get; set; } = "default";
    public string DisplayName { get; set; } = "Default Client";
    public string MqttHost { get; set; } = "localhost";
    public int MqttPort { get; set; } = 1883;
    public string SyncDirectory { get; set; } = "./sync";
    public string IgnoreFile { get; set; } = ".mcgoignore";
    public string EncryptionKey { get; set; } = "";
    public string ClientPrivateKey { get; set; } = "keys/client_private.pem";
    public string LogLevel { get; set; } = "INFO";
    public string LogFile { get; set; } = "";
}
