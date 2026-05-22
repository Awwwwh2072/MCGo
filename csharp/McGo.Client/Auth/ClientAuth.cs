using System.Security.Cryptography;
using McGo.Client.Crypto;

namespace McGo.Client.Auth;

public sealed class ClientAuth
{
    private readonly RSA _privateKey;
    private byte[]? _pendingChallenge;

    public ClientAuth(string privateKeyPath)
    {
        _privateKey = RsaSigner.LoadPrivateKeyFromPem(privateKeyPath);
    }

    public byte[]? HandleChallenge(IReadOnlyDictionary<string, object?> payload)
    {
        var challengeB64 = GetString(payload, "challenge");
        if (string.IsNullOrEmpty(challengeB64))
        {
            _pendingChallenge = null;
            return null;
        }

        try
        {
            _pendingChallenge = Convert.FromBase64String(challengeB64);
            return _pendingChallenge;
        }
        catch
        {
            _pendingChallenge = null;
            return null;
        }
    }

    public IReadOnlyDictionary<string, object?> BuildResponse()
    {
        if (_pendingChallenge is null)
            throw new AuthException("No pending challenge to respond to");
        var signature = RsaSigner.SignChallenge(_privateKey, _pendingChallenge);
        _pendingChallenge = null;
        var sigB64 = Convert.ToBase64String(signature);
        return new Dictionary<string, object?> { ["signature"] = sigB64 };
    }

    private static string GetString(IReadOnlyDictionary<string, object?> d, string key)
    {
        if (!d.TryGetValue(key, out var v) || v is null)
            return "";
        return v switch
        {
            string s => s,
            _ => v.ToString() ?? "",
        };
    }
}

public sealed class AuthException : Exception
{
    public AuthException(string message) : base(message) { }
}
