using System.Text.Json;

namespace McGo.Client.Protocol;

public static class MessageSerializer
{
    private static readonly JsonSerializerOptions Options = new()
    {
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    public static byte[] SerializeMessage(IReadOnlyDictionary<string, object?> data)
    {
        return JsonSerializer.SerializeToUtf8Bytes(data, Options);
    }

    public static byte[] SerializeMessage(object data)
    {
        return JsonSerializer.SerializeToUtf8Bytes(data, Options);
    }

    public static Dictionary<string, object?> DeserializeMessage(ReadOnlySpan<byte> payload)
    {
        try
        {
            var doc = JsonSerializer.Deserialize<JsonElement>(payload);
            if (doc.ValueKind != JsonValueKind.Object)
                return new Dictionary<string, object?>();
            return JsonElementToDict(doc);
        }
        catch
        {
            return new Dictionary<string, object?>();
        }
    }

    private static Dictionary<string, object?> JsonElementToDict(JsonElement el)
    {
        var d = new Dictionary<string, object?>();
        foreach (var p in el.EnumerateObject())
            d[p.Name] = JsonElementToObject(p.Value);
        return d;
    }

    private static object? JsonElementToObject(JsonElement el) => el.ValueKind switch
    {
        JsonValueKind.String => el.GetString(),
        JsonValueKind.Number => el.TryGetInt64(out var l) ? l : el.GetDouble(),
        JsonValueKind.True => true,
        JsonValueKind.False => false,
        JsonValueKind.Null => null,
        JsonValueKind.Object => JsonElementToDict(el),
        JsonValueKind.Array => el.EnumerateArray().Select(JsonElementToObject).ToList(),
        _ => null,
    };

    public static string NewFileId() => Guid.NewGuid().ToString("N");
}
