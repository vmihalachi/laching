namespace Webling.Images;

/// <summary>
/// Replays the bytes already read to check a file's signature, then the rest of the upload, and fails once the
/// total passes the limit. Uploads stream to Blob Storage through it instead of sitting in memory.
/// </summary>
public sealed class UploadStream(ReadOnlyMemory<byte> prefix, Stream rest, long limit) : Stream
{
    private int prefixRead;

    /// <summary>
    /// The bytes read so far.
    /// </summary>
    public long BytesRead { get; private set; }

    public bool LimitExceeded { get; private set; }

    public override bool CanRead => true;

    public override bool CanSeek => false;

    public override bool CanWrite => false;

    public override long Length => throw new NotSupportedException();

    public override long Position
    {
        get => BytesRead;
        set => throw new NotSupportedException();
    }

    public override int Read(byte[] buffer, int offset, int count) => Read(buffer.AsSpan(offset, count));

    public override int Read(Span<byte> buffer)
    {
        var read = prefixRead < prefix.Length ? ReadPrefix(buffer) : rest.Read(buffer);
        return Count(read);
    }

    public override Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken cancellationToken) =>
        ReadAsync(buffer.AsMemory(offset, count), cancellationToken).AsTask();

    public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
    {
        var read = prefixRead < prefix.Length ? ReadPrefix(buffer.Span) : await rest.ReadAsync(buffer, cancellationToken);
        return Count(read);
    }

    public override void Flush()
    {
    }

    public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();

    public override void SetLength(long value) => throw new NotSupportedException();

    public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();

    private int ReadPrefix(Span<byte> buffer)
    {
        var count = Math.Min(buffer.Length, prefix.Length - prefixRead);
        prefix.Span.Slice(prefixRead, count).CopyTo(buffer);
        prefixRead += count;
        return count;
    }

    private int Count(int read)
    {
        BytesRead += read;
        if (BytesRead > limit)
        {
            LimitExceeded = true;
            throw new IOException($"The upload is larger than {limit} bytes.");
        }

        return read;
    }
}
