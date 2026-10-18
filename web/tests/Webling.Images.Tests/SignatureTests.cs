namespace Webling.Images.Tests;

public sealed class SignatureTests
{
    [Theory]
    [InlineData(new byte[] { 0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0, 0, 0, 13 }, true)]
    [InlineData(new byte[] { 0xFF, 0xD8, 0xFF, 0xE0, 0, 16, 0x4A, 0x46, 0x49, 0x46, 0, 1 }, true)]
    [InlineData(new byte[] { 0x52, 0x49, 0x46, 0x46, 1, 2, 3, 4, 0x57, 0x45, 0x42, 0x50 }, true)]
    [InlineData(new byte[] { 0x52, 0x49, 0x46, 0x46, 1, 2, 3, 4, 0x57, 0x41, 0x56, 0x45 }, false)] // RIFF WAVE
    [InlineData(new byte[] { 0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 0, 0, 0, 0, 0, 0 }, false)] // GIF
    [InlineData(new byte[] { 0x3C, 0x73, 0x76, 0x67 }, false)] // <svg
    [InlineData(new byte[0], false)]
    public void Recognizes_PNG_JPEG_and_WebP_by_their_first_bytes(byte[] header, bool supported)
    {
        Assert.Equal(supported, ImageSignature.IsSupported(header));
    }

    [Theory]
    [InlineData("heic")]
    [InlineData("heix")]
    [InlineData("mif1")]
    public void Recognizes_HEIF_brands(string brand)
    {
        byte[] header = [0, 0, 0, 0x18, .. "ftyp"u8, .. System.Text.Encoding.ASCII.GetBytes(brand)];

        Assert.True(ImageSignature.IsHeif(header));
    }

    [Fact]
    public void An_MP4_is_not_HEIF()
    {
        Assert.False(ImageSignature.IsHeif([0, 0, 0, 0x18, .. "ftypisom"u8]));
    }

    [Fact]
    public void Recognizes_PDF()
    {
        Assert.True(DocumentSignature.IsPdf("%PDF-1.7"u8));
        Assert.False(DocumentSignature.IsPdf("%PDX-"u8));
    }
}
