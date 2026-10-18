using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Npgsql;

namespace Webling.Jobs.Tests;

public sealed class TestDbContext(DbContextOptions<TestDbContext> options) : DbContext(options)
{
    protected override void OnModelCreating(ModelBuilder modelBuilder) => modelBuilder.ApplyWeblingJobs();
}

public sealed record TestJob(int Number) : IJob
{
    public static string JobType => "test";
}

/// <summary>
/// A database of its own for one test, created with the jobs table and dropped afterwards.
/// </summary>
public sealed class TestDatabase : IAsyncDisposable
{
    private static readonly string ServerConnectionString =
        Environment.GetEnvironmentVariable("WEBLING_TEST_POSTGRES") is { Length: > 0 } configured
            ? configured
            : "Host=localhost;Port=5432;Username=postgres;Password=postgres";

    private readonly string name = "webling_" + Guid.NewGuid().ToString("N");

    private TestDatabase()
    {
        ConnectionString = new NpgsqlConnectionStringBuilder(ServerConnectionString) { Database = name, Pooling = false }.ConnectionString;
    }

    public string ConnectionString { get; }

    public static async Task<TestDatabase> CreateAsync()
    {
        var database = new TestDatabase();
        await using var context = database.Context();
        await context.Database.EnsureCreatedAsync();
        return database;
    }

    public TestDbContext Context() =>
        new(new DbContextOptionsBuilder<TestDbContext>().UseNpgsql(ConnectionString).Options);

    /// <summary>
    /// A host with the dispatcher, a 50 ms poll and whatever <paramref name="configure"/> adds.
    /// </summary>
    public IHost Host(Action<JobDispatcherOptions>? options = null, Action<IServiceCollection>? configure = null)
    {
        var builder = Microsoft.Extensions.Hosting.Host.CreateEmptyApplicationBuilder(new HostApplicationBuilderSettings());
        builder.Logging.SetMinimumLevel(LogLevel.Warning);
        builder.Services.AddDbContext<TestDbContext>(context => context.UseNpgsql(ConnectionString));
        builder.Services.AddJobDispatcher<TestDbContext>();
        builder.Services.Configure<JobDispatcherOptions>(dispatcher =>
        {
            dispatcher.PollInterval = TimeSpan.FromMilliseconds(50);
            options?.Invoke(dispatcher);
        });
        configure?.Invoke(builder.Services);
        return builder.Build();
    }

    public async Task<List<Job>> JobsAsync()
    {
        await using var context = Context();
        return await context.Set<Job>().AsNoTracking().OrderBy(job => job.CreatedAt).ToListAsync();
    }

    /// <summary>
    /// Polls the jobs table until <paramref name="done"/> holds, for up to 30 seconds.
    /// </summary>
    public async Task<List<Job>> WaitForAsync(Func<List<Job>, bool> done)
    {
        var deadline = DateTime.UtcNow.AddSeconds(30);
        while (true)
        {
            var jobs = await JobsAsync();
            if (done(jobs))
            {
                return jobs;
            }

            if (DateTime.UtcNow > deadline)
            {
                throw new TimeoutException("Jobs never reached the expected state: " +
                    string.Join(", ", jobs.Select(job => $"{job.Status}/{job.Attempts}")));
            }

            await Task.Delay(50);
        }
    }

    public async ValueTask DisposeAsync()
    {
        await using var connection = new NpgsqlConnection(ServerConnectionString);
        await connection.OpenAsync();
        await using var drop = new NpgsqlCommand($"DROP DATABASE IF EXISTS \"{name}\" WITH (FORCE)", connection);
        await drop.ExecuteNonQueryAsync();
    }
}
