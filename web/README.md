# Laching.Web

Building blocks for small ASP.NET Core and Blazor sites on Azure Container Apps behind Cloudflare: the parts every
such site needs and nobody wants to write twice. The web side of [Laching](https://github.com/vmihalachi/laching),
in its `web/` folder. .NET 10, MIT.

It grew out of Adjudeca, a Blazor site in three languages, which runs on it, and was published as `Webling.*` until it
moved into Laching. It is `0.x`, so any minor version may change public types.

**No support.** Built for my own projects and shared as is: issues and pull requests from others aren't monitored,
and any release may change or break things.

## What's inside

| Package | Depends on | What it does |
|---|---|---|
| `Laching.Web.Jobs` | EF Core, hosting abstractions | A PostgreSQL job table. Jobs are enqueued in the same `SaveChanges` as the change that caused them, claimed with `FOR UPDATE SKIP LOCKED` so replicas never run the same job, leased for 5 minutes, retried after 30 s × 2^(attempt − 1) (capped at 1 h, plus up to 20% jitter) and marked failed after 5 attempts. A job interrupted by shutdown gets its attempt back. Done jobs are deleted after 7 days, failed ones after 30. |
| `Laching.Web.AspNetCore` | ASP.NET Core | Behind Cloudflare: an origin check that answers 403 without the shared secret, hides health endpoints from requests through Cloudflare and trusts `CF-Connecting-IP` only after the check; security headers with a per-request CSP nonce; shared-cache headers for `[PublicPage]` components and documents that stay private for visitors with a personal cookie. |
| `Laching.Web.Cloudflare` | `HttpClient` | Cloudflare's APIs without ASP.NET Core, so a worker can use them: Turnstile verification behind `IHumanCheck` (a missing, reused or failed token and any network failure are refused), and cache purges by address in batches of 30. |
| `Laching.Web.Aspire` | Aspire.Hosting.Azure.AppContainers | AppHost pieces: Container Apps ingress that accepts only Cloudflare's IPv4 ranges (bundled, with a check against cloudflare.com for deploys) with an Origin CA certificate, and a deployment step that runs a Container Apps job, such as migrations, before the apps roll out. |
| `Laching.Web.Tailwind` | nothing (build only) | Tailwind CSS v4 in `dotnet build`, without Node: the pinned standalone CLI is downloaded once per user into `~/.laching/`, checked against its SHA-256, and run before each build when a style or component changed. |
| `Laching.Web.Images` | SkiaSharp | Uploads: PNG, JPEG and WebP recognized by magic bytes, HEIC recognized and refused, a stream that stops at a size limit, and re-encoding to upright, metadata-free sRGB WebP in several sizes or to JPEG on white. |

Every package ships no strings and no settings of its own: the app passes them in, and secrets come from
configuration, never from code.

## Install

```sh
dotnet add package Laching.Web.Jobs
dotnet add package Laching.Web.AspNetCore
dotnet add package Laching.Web.Cloudflare
dotnet add package Laching.Web.Aspire        # in the AppHost
dotnet add package Laching.Web.Tailwind      # build only; nothing ships with the app
dotnet add package Laching.Web.Images
```

One version for all six (`LachingWebVersion`), so keep them on the same one. It moves only in a Laching release
that changed them, so it can trail Laching's tag.

## Using it

- **Jobs.** Call `modelBuilder.ApplyLachingWebJobs()` in `OnModelCreating` and add a migration: it maps the `jobs`
  table with explicit snake_case names, whatever naming convention the rest of the model uses. Jobs are records
  implementing `IJob` (`static string JobType => "send-mail"`); enqueue with `db.EnqueueJob(job, now)` before
  `SaveChanges`. In the worker, `services.AddJobDispatcher<AppDbContext>(configuration.GetSection("Jobs"))` and
  `services.AddJobHandler<SendMail, SendMailHandler>()`. Handlers must be idempotent: a job can run more than once.
  Trace `Laching.Web.Jobs` to see one activity per run.
- **Origin.** `builder.AddCloudflareOrigin()` and `app.UseCloudflareOrigin()` first in the pipeline. A Cloudflare
  Transform Rule sends the secret in `X-Origin-Secret`; outside Development the app refuses to start without
  `Cloudflare:OriginSecret` (32+ characters).
- **Headers.** `app.UseSecurityHeaders(nonce => $"script-src 'self' 'nonce-{nonce}'", reportOnly: false)`; components read
  the nonce with `HttpContext.GetCspNonce()`.
- **Caching.** `services.AddPublicCaching("__Host-session", "consent")` names the cookies that personalize a page;
  `app.UsePublicPageCaching()` after authentication; mark pages `@attribute [PublicPage]`. Other endpoints call
  `PublicCaching.Apply(context, PublicCaching.PageCacheControl, varyByCookie: true)`.
- **Cloudflare.** `services.AddTurnstile(configuration.GetSection("Turnstile"))` registers `IHumanCheck`;
  `services.AddCloudflareCache(configuration.GetSection("Cloudflare"))` registers `CloudflareCache`, which does
  nothing until `ZoneId`, `PurgeToken` and `PublicOrigin` are set.
- **AppHost.** In `PublishAsAzureContainerApp`, `app.UseCloudflareIngress(publicHostname, certificateName)`; when
  publishing, `await CloudflareIngress.EnsureRangesCurrentAsync()` fails the deployment if Cloudflare changed its
  ranges. `builder.RunJobBeforeApps("migrations", subscription, resourceGroup, "web", "worker")` runs the job with
  the Azure CLI after it is provisioned and before the apps are.
- **Tailwind.** Reference the package with `PrivateAssets="all"`, put `@import "tailwindcss";` in
  `Styles/app.css` and link `css/app.css`. Override `TailwindInput`, `TailwindOutput` or the `TailwindSource` items
  if the layout differs.
- **Images.** Check the first `ImageSignature.Length` bytes with `ImageSignature.IsSupported` and `IsHeif`, stream the
  rest through `new UploadStream(header, body, limit)`, and in the worker call `ImageEncoder.Encode(bytes, [1600, 800],
  quality)` or `ImageEncoder.Jpeg(bytes, quality)`.

## Develop

From the repository's root:

```sh
dotnet test web/Laching.Web.sln     # Jobs tests need PostgreSQL: LACHING_WEB_TEST_POSTGRES, or localhost:5432 postgres/postgres
dotnet format web/Laching.Web.sln --verify-no-changes
web/scripts/tailwind-sample.sh      # packs Laching.Web.Tailwind and builds the sample with it
scripts/verify.sh                   # what applies to your changes, web/ included
```

A throwaway server for the Jobs tests:
`docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=postgres postgres:16` and
`LACHING_WEB_TEST_POSTGRES="Host=localhost;Port=55432;Username=postgres;Password=postgres"`.

Releases are Laching's (`release.yml`): the web packages ship in a release when `web/src` or `web/`'s build files
changed since `LachingWebVersion`.

## License

[MIT](https://github.com/vmihalachi/laching/blob/master/LICENSE). © 2026 Vlad Mihalachi.
