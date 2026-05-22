using System.IO.Compression;

namespace McGo.Client.Crypto;

public static class ZlibHelper
{
    public static byte[] Decompress(ReadOnlySpan<byte> data)
    {
        try
        {
            using var input = new MemoryStream(data.ToArray());
            using var zlib = new ZLibStream(input, CompressionMode.Decompress);
            using var output = new MemoryStream();
            zlib.CopyTo(output);
            return output.ToArray();
        }
        catch (Exception ex)
        {
            throw new CryptoException($"Decompression failed: {ex.Message}");
        }
    }
}
