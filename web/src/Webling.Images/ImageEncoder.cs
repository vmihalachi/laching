using SkiaSharp;

namespace Webling.Images;

/// <summary>
/// Re-encodes uploaded images. Decoding and encoding again drops metadata, EXIF location included, and anything
/// that is not the picture. Sources over 64 megapixels, or that would decode to more than 16, are refused.
/// </summary>
public static class ImageEncoder
{
    // Larger sources are refused before decoding.
    private const long MaxSourcePixels = 64_000_000;

    // Bounds the memory of one decode: 16 MP of RGBA is 64 MB.
    private const long MaxDecodedPixels = 16_000_000;

    /// <summary>
    /// Decodes the image once, turns it upright and converts it to sRGB, then encodes one WebP for each longest
    /// side in <paramref name="maxSides"/>, in that order, without enlarging. Returns <see langword="null"/> for
    /// data that is not a readable image or is too large.
    /// </summary>
    public static IReadOnlyList<EncodedImage>? Encode(byte[] source, IReadOnlyList<int> maxSides, int quality)
    {
        using var data = SKData.CreateCopy(source);
        using var codec = SKCodec.Create(data);
        if (codec is null || codec.Info.Width <= 0 || codec.Info.Height <= 0
            || (long)codec.Info.Width * codec.Info.Height > MaxSourcePixels)
        {
            return null;
        }

        var largest = Math.Min(maxSides.Max(), Math.Max(codec.Info.Width, codec.Info.Height));
        var decodeSize = DecodeSize(codec, largest);
        if ((long)decodeSize.Width * decodeSize.Height > MaxDecodedPixels)
        {
            return null;
        }

        // Phones save Display P3; converting to sRGB keeps the colours right in browsers that read the file as sRGB.
        using var srgb = SKColorSpace.CreateSrgb();
        var info = new SKImageInfo(decodeSize.Width, decodeSize.Height, SKImageInfo.PlatformColorType, SKAlphaType.Premul, srgb);
        using var decoded = SKBitmap.Decode(codec, info);
        if (decoded is null)
        {
            return null;
        }

        var results = new Dictionary<int, EncodedImage>();
        var current = Orient(decoded, codec.EncodedOrigin);
        try
        {
            // Each smaller size is scaled from the previous one, so no step shrinks by much more than half and the
            // cubic filter does not alias.
            foreach (var side in maxSides.Distinct().OrderDescending())
            {
                var fit = Math.Min(1f, (float)side / Math.Max(current.Width, current.Height));
                var target = new SKSizeI(Math.Max(1, (int)MathF.Round(current.Width * fit)), Math.Max(1, (int)MathF.Round(current.Height * fit)));
                if (target != new SKSizeI(current.Width, current.Height))
                {
                    var resized = current.Resize(target, new SKSamplingOptions(SKCubicResampler.Mitchell));
                    if (resized is null)
                    {
                        return null;
                    }

                    current.Dispose();
                    current = resized;
                }

                if (Webp(current, quality) is not { } bytes)
                {
                    return null;
                }

                results[side] = new EncodedImage(bytes, current.Width, current.Height);
            }
        }
        finally
        {
            current.Dispose();
        }

        return maxSides.Select(side => results[side]).ToList();
    }

    /// <summary>
    /// Re-encodes an image as a JPEG of the same size on white, so a picture with transparency does not turn black:
    /// for places that take only JPEG, such as social networks' uploads and phone galleries. The source should be
    /// upright and sRGB already, as <see cref="Encode"/> writes it. Returns <see langword="null"/> for data that is
    /// not a readable image or is too large.
    /// </summary>
    public static byte[]? Jpeg(byte[] source, int quality)
    {
        using var data = SKData.CreateCopy(source);
        using var codec = SKCodec.Create(data);
        if (codec is null || codec.Info.Width <= 0 || codec.Info.Height <= 0
            || (long)codec.Info.Width * codec.Info.Height > MaxDecodedPixels)
        {
            return null;
        }

        using var srgb = SKColorSpace.CreateSrgb();
        var info = new SKImageInfo(codec.Info.Width, codec.Info.Height, SKImageInfo.PlatformColorType, SKAlphaType.Premul, srgb);
        using var decoded = SKBitmap.Decode(codec, info);
        if (decoded is null)
        {
            return null;
        }

        // Untagged, like the WebPs: the pixels are sRGB, and no ICC profile or other metadata is written.
        using var opaque = new SKBitmap(new SKImageInfo(decoded.Width, decoded.Height, SKColorType.Rgba8888, SKAlphaType.Opaque));
        using (var canvas = new SKCanvas(opaque))
        {
            canvas.Clear(SKColors.White);
            canvas.DrawBitmap(decoded, 0, 0, SKSamplingOptions.Default);
        }

        using var pixmap = opaque.PeekPixels();
        using var encoded = pixmap?.Encode(new SKJpegEncoderOptions(quality, SKJpegEncoderDownsample.Downsample420, SKJpegEncoderAlphaOption.Ignore));
        return encoded?.ToArray();
    }

    // The smallest size the codec decodes to directly whose longest side still covers the target. JPEG decodes at
    // eighths of the full size and WebP at any scale; other formats only at full size.
    private static SKSizeI DecodeSize(SKCodec codec, int target)
    {
        for (var eighths = 1; eighths < 8; eighths++)
        {
            var size = codec.GetScaledDimensions(eighths / 8f);
            if (Math.Max(size.Width, size.Height) >= target)
            {
                return size;
            }
        }

        return codec.Info.Size;
    }

    // The pixels are sRGB already; encoding them untagged keeps an ICC profile out of every file.
    private static byte[]? Webp(SKBitmap bitmap, int quality)
    {
        using var pixmap = bitmap.PeekPixels();
        if (pixmap is null)
        {
            return null;
        }

        using var untagged = new SKPixmap(pixmap.Info with { ColorSpace = null }, pixmap.GetPixels(), pixmap.RowBytes);
        using var encoded = untagged.Encode(new SKWebpEncoderOptions(SKWebpEncoderCompression.Lossy, quality));
        return encoded?.ToArray();
    }

    // Applies the EXIF orientation, so a phone photo is stored the way it was taken. The transforms map
    // source pixels to the upright image.
    private static SKBitmap Orient(SKBitmap bitmap, SKEncodedOrigin origin)
    {
        var (width, height) = (bitmap.Width, bitmap.Height);
        var swaps = origin is SKEncodedOrigin.LeftTop or SKEncodedOrigin.RightTop or SKEncodedOrigin.RightBottom or SKEncodedOrigin.LeftBottom;
        var result = new SKBitmap(bitmap.Info.WithSize(swaps ? height : width, swaps ? width : height));

        using var canvas = new SKCanvas(result);
        switch (origin)
        {
            case SKEncodedOrigin.TopRight:
                canvas.Translate(width, 0);
                canvas.Scale(-1, 1);
                break;
            case SKEncodedOrigin.BottomRight:
                canvas.Translate(width, height);
                canvas.Scale(-1, -1);
                break;
            case SKEncodedOrigin.BottomLeft:
                canvas.Translate(0, height);
                canvas.Scale(1, -1);
                break;
            case SKEncodedOrigin.LeftTop:
                canvas.Scale(-1, 1);
                canvas.RotateDegrees(90);
                break;
            case SKEncodedOrigin.RightTop:
                canvas.Translate(height, 0);
                canvas.RotateDegrees(90);
                break;
            case SKEncodedOrigin.RightBottom:
                canvas.Translate(height, width);
                canvas.Scale(1, -1);
                canvas.RotateDegrees(90);
                break;
            case SKEncodedOrigin.LeftBottom:
                canvas.Translate(0, width);
                canvas.RotateDegrees(-90);
                break;
            default:
                break;
        }

        // Quarter turns and mirrors land on whole pixels, so no filtering is needed.
        canvas.DrawBitmap(bitmap, 0, 0, SKSamplingOptions.Default);
        return result;
    }
}

public sealed record EncodedImage(byte[] Bytes, int Width, int Height);
