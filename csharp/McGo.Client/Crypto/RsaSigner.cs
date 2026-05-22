using System.Security.Cryptography;

namespace McGo.Client.Crypto;

public static class RsaSigner
{
    public static byte[] SignChallenge(RSA privateKey, ReadOnlySpan<byte> challenge)
    {
        return privateKey.SignData(challenge, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
    }

    public static RSA LoadPrivateKeyFromPem(string path)
    {
        if (!File.Exists(path))
            throw new CryptoException($"Private key file not found: {path}");
        var pem = File.ReadAllText(path);
        var rsa = RSA.Create();
        rsa.ImportFromPem(pem);
        return rsa;
    }
}

public sealed class CryptoException : Exception
{
    public CryptoException(string message) : base(message) { }
}
