using Microsoft.EntityFrameworkCore;

namespace Webling.Jobs;

public static class JobModelBuilderExtensions
{
    /// <summary>
    /// Maps <see cref="Job"/> to the <c>jobs</c> table. Table, column, key and index names are explicit snake_case,
    /// so the dispatcher's raw claim SQL works whatever naming convention the rest of the model uses. Call it from
    /// <c>OnModelCreating</c> of the context the dispatcher uses; the payload column is PostgreSQL <c>jsonb</c>.
    /// </summary>
    public static ModelBuilder ApplyWeblingJobs(this ModelBuilder modelBuilder)
    {
        ArgumentNullException.ThrowIfNull(modelBuilder);

        modelBuilder.Entity<Job>(builder =>
        {
            builder.ToTable("jobs");
            builder.HasKey(job => job.Id).HasName("pk_jobs");
            builder.Property(job => job.Id).HasColumnName("id").ValueGeneratedNever();
            builder.Property(job => job.Type).HasColumnName("type").HasMaxLength(100);
            builder.Property(job => job.Payload).HasColumnName("payload").HasColumnType("jsonb");
            builder.Property(job => job.Status)
                .HasColumnName("status")
                .HasMaxLength(20)
                .HasConversion(status => status.ToString().ToLowerInvariant(), value => Enum.Parse<JobStatus>(value, true));
            builder.Property(job => job.Attempts).HasColumnName("attempts");
            builder.Property(job => job.RunAfter).HasColumnName("run_after");
            builder.Property(job => job.LockedUntil).HasColumnName("locked_until");
            builder.Property(job => job.LastError).HasColumnName("last_error");
            builder.Property(job => job.CreatedAt).HasColumnName("created_at");
            builder.Property(job => job.UpdatedAt).HasColumnName("updated_at");

            // Serves the dispatcher's claim query: due pending jobs and expired running leases.
            builder.HasIndex(job => new { job.Status, job.RunAfter }).HasDatabaseName("ix_jobs_status_run_after");
        });

        return modelBuilder;
    }
}
