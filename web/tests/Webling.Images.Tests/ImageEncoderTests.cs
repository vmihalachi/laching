using SkiaSharp;

namespace Webling.Images.Tests;

public sealed class ImageEncoderTests
{
    [Fact]
    public void A_rotated_phone_photo_is_stored_upright_without_its_metadata()
    {
        // Stored 40 wide and 20 high, red on the left; orientation 6 says "turn 90° clockwise to show it".
        var source = Samples.JpegWithExif(40, 20, orientation: 6);
        Assert.True(Samples.ContainsAscii(source, "Exif"));

        var image = Assert.Single(ImageEncoder.Encode(source, [100], quality: 90)!);

        Assert.Equal((20, 40), (image.Width, image.Height));
        Assert.False(Samples.ContainsAscii(image.Bytes, "Exif"));
        Assert.False(Samples.ContainsAscii(image.Bytes, "EXIF"));
        Assert.False(Samples.ContainsAscii(image.Bytes, "ICCP"));
        using var decoded = SKBitmap.Decode(image.Bytes);
        Assert.True(IsRed(decoded.GetPixel(10, 5)), "the left half of the stored image is now on top");
        Assert.True(IsBlue(decoded.GetPixel(10, 35)), "the right half is now at the bottom");
    }

    [Fact]
    public void Each_size_fits_its_longest_side_without_enlarging()
    {
        var source = Samples.JpegWithExif(400, 200, orientation: 1);

        var images = ImageEncoder.Encode(source, [100, 1000, 50], quality: 80)!;

        Assert.Equal([(100, 50), (400, 200), (50, 25)], images.Select(image => (image.Width, image.Height)));
        Assert.All(images, image => Assert.True(ImageSignature.IsSupported(image.Bytes) && Samples.ContainsAscii(image.Bytes[..16], "WEBP")));
    }

    [Fact]
    public void A_PNG_with_transparency_becomes_a_JPEG_on_white()
    {
        var jpeg = ImageEncoder.Jpeg(Samples.TransparentPng(40), quality: 90)!;

        Assert.Equal([0xFF, 0xD8, 0xFF], jpeg[..3]);
        using var decoded = SKBitmap.Decode(jpeg);
        Assert.Equal((40, 40), (decoded.Width, decoded.Height));
        var corner = decoded.GetPixel(2, 2);
        Assert.True(corner.Red > 245 && corner.Green > 245 && corner.Blue > 245, $"corner is {corner}, not white");
        Assert.True(IsRed(decoded.GetPixel(20, 20)));
    }

    [Fact]
    public void A_HEIC_photo_is_recognized_and_cannot_be_encoded()
    {
        var heic = Samples.HeicHeader();

        Assert.True(ImageSignature.IsHeif(heic));
        Assert.False(ImageSignature.IsSupported(heic));
        Assert.Null(ImageEncoder.Encode(heic, [100], quality: 80));
    }

    [Fact]
    public void An_oversized_source_is_refused_before_decoding()
    {
        // The header alone is readable, so the codec knows the size without any pixels.
        using (var codec = SKCodec.Create(new MemoryStream(Samples.PngHeader(9000, 9000))))
        {
            Assert.NotNull(codec);
            Assert.Equal(9000, codec.Info.Width);
        }

        Assert.Null(ImageEncoder.Encode(Samples.PngHeader(9000, 9000), [1000], quality: 80));
        Assert.Null(ImageEncoder.Jpeg(Samples.PngHeader(5000, 4000), quality: 80));
    }

    [Fact]
    public void Data_that_is_not_an_image_is_refused()
    {
        Assert.Null(ImageEncoder.Encode("%PDF-1.7 not an image"u8.ToArray(), [100], quality: 80));
        Assert.Null(ImageEncoder.Jpeg([1, 2, 3], quality: 80));
    }

    private static bool IsRed(SKColor color) => color.Red > 200 && color.Green < 60 && color.Blue < 60;

    private static bool IsBlue(SKColor color) => color.Blue > 200 && color.Red < 60 && color.Green < 60;
}
