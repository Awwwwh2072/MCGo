using System.Security.Cryptography;

namespace McGo.Client.Crypto;

public static class AesGcmHelper
{
    private const int NonceLength = 12;

    public static byte[] Decrypt(ReadOnlySpan<byte> key, ReadOnlySpan<byte> encrypted, ReadOnlySpan<byte> aad)
    {
        if (encrypted.Length < NonceLength + 16)
            throw new CryptoException("Encrypted data too short");
        var nonce = encrypted[..NonceLength];
        var ctWithTag = encrypted[NonceLength..];
        var tag = ctWithTag[^16..];
        var ciphertext = ctWithTag[..^16];
        var plaintext = new byte[ciphertext.Length];
        using var aes = new AesGcm(key, 16);
        aes.Decrypt(nonce, ciphertext, tag, plaintext, aad);
        return plaintext;
    }
}
