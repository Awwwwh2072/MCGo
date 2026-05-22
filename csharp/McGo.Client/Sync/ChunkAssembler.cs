namespace McGo.Client.Sync;

public static class ChunkAssembler
{
    public const int DefaultChunkSize = 65536;

    public static byte[] ReassembleChunks(IReadOnlyDictionary<int, byte[]> chunks)
    {
        using var ms = new MemoryStream();
        foreach (var seq in chunks.Keys.OrderBy(k => k))
        {
            if (chunks.TryGetValue(seq, out var b))
                ms.Write(b, 0, b.Length);
        }

        return ms.ToArray();
    }
}
