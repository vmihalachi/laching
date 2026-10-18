using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Npgsql;

namespace Webling.Jobs.Tests;

public sealed class TableTests
{
    [Fact]
    public async Task EnqueueJob_writes_a_pending_row_with_the_JSON_payload()
    {
        await using var database = await TestDatabase.CreateAsync();
        var now = new DateTimeOffset(2026, 10, 9, 8, 0, 0, TimeSpan.Zero);
        await using (var context = database.Context())
        {
            context.EnqueueJob(new TestJob(42), now, now.AddMinutes(5));
            await context.SaveChangesAsync();
        }

        await using var connection = new NpgsqlConnection(database.ConnectionString);
        await connection.OpenAsync();
        await using var command = new NpgsqlCommand("SELECT type, payload::text, status, attempts, run_after, locked_until FROM jobs", connection);
        await using var reader = await command.ExecuteReaderAsync();
        Assert.True(await reader.ReadAsync());
        Assert.Equal("test", reader.GetString(0));
        Assert.Equal("""{"number": 42}""", reader.GetString(1));
        Assert.Equal("pending", reader.GetString(2));
        Assert.Equal(0, reader.GetInt32(3));
        Assert.Equal(now.AddMinutes(5), reader.GetFieldValue<DateTimeOffset>(4));
        Assert.True(reader.IsDBNull(5));
    }

    [Fact]
    public void The_mapping_is_the_same_under_a_snake_case_naming_convention()
    {
        static IEnumerable<string> Names(DbContextOptions<TestDbContext> options)
        {
            using var context = new TestDbContext(options);
            var entity = context.Model.FindEntityType(typeof(Job))!;
            var table = Microsoft.EntityFrameworkCore.Metadata.StoreObjectIdentifier.Table("jobs");
            return entity.GetProperties().Select(property => property.GetColumnName(table)!)
                .Append(entity.GetTableName()!)
                .Append(entity.FindPrimaryKey()!.GetName()!)
                .Concat(entity.GetIndexes().Select(index => index.GetDatabaseName()!))
                .Order(StringComparer.Ordinal);
        }

        var plain = new DbContextOptionsBuilder<TestDbContext>().UseNpgsql("Host=unused").Options;
        var snake = new DbContextOptionsBuilder<TestDbContext>().UseNpgsql("Host=unused").UseSnakeCaseNamingConvention().Options;

        Assert.Equal(Names(plain), Names(snake));
        Assert.Contains("run_after", Names(plain));
        Assert.Contains("ix_jobs_status_run_after", Names(plain));
    }

    [Fact]
    public async Task Cleanup_deletes_old_done_and_failed_jobs_only()
    {
        await using var database = await TestDatabase.CreateAsync();
        var now = DateTimeOffset.UtcNow;
        await using (var context = database.Context())
        {
            void Add(int number, JobStatus status, TimeSpan age)
            {
                var job = context.EnqueueJob(new TestJob(number), now - age);
                job.Status = status;
            }

            Add(1, JobStatus.Done, TimeSpan.FromDays(8));
            Add(2, JobStatus.Done, TimeSpan.FromDays(6));
            Add(3, JobStatus.Failed, TimeSpan.FromDays(31));
            Add(4, JobStatus.Failed, TimeSpan.FromDays(29));
            Add(5, JobStatus.Pending, TimeSpan.FromDays(40));
            Add(6, JobStatus.Running, TimeSpan.FromDays(40));
            await context.SaveChangesAsync();
        }

        using var host = database.Host();
        var cleanup = new JobCleanup<TestDbContext>(
            host.Services.GetRequiredService<IServiceScopeFactory>(),
            Options.Create(new JobDispatcherOptions()),
            TimeProvider.System,
            NullLogger<JobCleanup<TestDbContext>>.Instance);

        Assert.Equal(2, await cleanup.RunOnceAsync(CancellationToken.None));

        var left = (await database.JobsAsync()).Select(job => System.Text.Json.JsonSerializer.Deserialize<TestJob>(job.Payload, Job.PayloadSerializerOptions)!.Number);
        Assert.Equal([2, 4, 5, 6], left.Order());
    }

    [Fact]
    public async Task The_dispatcher_host_runs_the_cleanup()
    {
        await using var database = await TestDatabase.CreateAsync();
        await using (var context = database.Context())
        {
            var job = context.EnqueueJob(new TestJob(1), DateTimeOffset.UtcNow.AddDays(-8));
            job.Status = JobStatus.Done;
            await context.SaveChangesAsync();
        }

        using var host = database.Host();
        await host.StartAsync();
        await database.WaitForAsync(jobs => jobs.Count == 0);
        await host.StopAsync();
    }
}
