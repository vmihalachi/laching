namespace Webling.Images;

/// <summary>
/// Recognizes the image formats the encoder accepts (PNG, JPEG, WebP) by their first bytes, whatever the file name or the
/// content type the browser sent.
/// </summary>
public static class ImageSignature
{
    /// <summary>
    /// The number of leading bytes <see cref="IsSupported"/> and <see cref="IsHeif"/> need.
    /// </summary>
    public const int Length = 12;

    private static ReadOnlySpan<byte> Png => [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A];

    private static ReadOnlySpan<byte> Jpeg => [0xFF, 0xD8, 0xFF];

    public static bool IsSupported(ReadOnlySpan<byte> header) =>
        header.StartsWith(Png)
        || header.StartsWith(Jpeg)
        || (header.Length >= Length && header.StartsWith("RIFF"u8) && header[8..12].SequenceEqual("WEBP"u8));

    /// <summary>
    /// Whether the bytes start a HEIC or HEIF photo, which SkiaSharp cannot decode: an ISO media file
    /// whose major brand is one of HEIF's.
    /// </summary>
    public static bool IsHeif(ReadOnlySpan<byte> header) =>
        header.Length >= Length
        && header[4..8].SequenceEqual("ftyp"u8)
        && header[8..12] is var brand
        && (brand.SequenceEqual("heic"u8) || brand.SequenceEqual("heix"u8) || brand.SequenceEqual("hevc"u8)
            || brand.SequenceEqual("hevx"u8) || brand.SequenceEqual("heim"u8) || brand.SequenceEqual("heis"u8)
            || brand.SequenceEqual("mif1"u8) || brand.SequenceEqual("msf1"u8));
}

/// <summary>
/// Recognizes PDF documents by their first bytes.
/// </summary>
public static class DocumentSignature
{
    public const int Length = 5;

    public static bool IsPdf(ReadOnlySpan<byte> header) => header.StartsWith("%PDF-"u8);
}
