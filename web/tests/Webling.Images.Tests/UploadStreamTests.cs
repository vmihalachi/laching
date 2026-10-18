namespace Webling.Images.Tests;

public sealed class UploadStreamTests
{
    private static readonly byte[] Prefix = [1, 2, 3, 4];
    private static readonly byte[] Rest = [5, 6, 7, 8, 9, 10, 11, 12, 13, 14];

    [Fact]
    public async Task Replays_the_prefix_then_the_rest_within_the_limit()
    {
        await using var upload = new UploadStream(Prefix, new MemoryStream(Rest), limit: 14);
        var copy = new MemoryStream();

        await upload.CopyToAsync(copy, bufferSize: 3);

        Assert.Equal(Prefix.Concat(Rest), copy.ToArray());
        Assert.Equal(14, upload.BytesRead);
        Assert.False(upload.LimitExceeded);
    }

    [Fact]
    public async Task Stops_once_the_total_passes_the_limit()
    {
        await using var upload = new UploadStream(Prefix, new MemoryStream(Rest), limit: 12);

        await Assert.ThrowsAsync<IOException>(() => upload.CopyToAsync(Stream.Null, bufferSize: 4));

        Assert.True(upload.LimitExceeded);
        Assert.InRange(upload.BytesRead, 13, 16);
    }

    [Fact]
    public void Stops_on_synchronous_reads_too()
    {
        using var upload = new UploadStream(Prefix, new MemoryStream(Rest), limit: 4);
        var buffer = new byte[8];

        Assert.Equal(4, upload.Read(buffer));
        Assert.Throws<IOException>(() => upload.Read(buffer));
        Assert.True(upload.LimitExceeded);
    }
}
