namespace Webling.AspNetCore;

/// <summary>
/// Marks a static Razor component page that anonymous visitors share, so a shared cache may keep it (see
/// <see cref="PublicCaching"/>). The page must render the same for every visitor without a personal cookie, and
/// must not post a form that needs an antiforgery token.
/// </summary>
[AttributeUsage(AttributeTargets.Class, Inherited = true)]
public sealed class PublicPageAttribute : Attribute;
