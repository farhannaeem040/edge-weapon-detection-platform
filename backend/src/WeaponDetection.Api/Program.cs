using System.Text.Json.Serialization;
using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Api.Filters;
using WeaponDetection.Api.Security;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Infrastructure;

var builder = WebApplication.CreateBuilder(args);

// Add services to the container.

builder.Services
    .AddControllers(options => options.Filters.Add<ApiEnvelopeResultFilter>())
    // Omits null Data/ErrorCode so the wire shape matches ARCH-001/IP-01 §11 exactly:
    // {success, message, data} on success, {success, message, errorCode} on failure.
    .AddJsonOptions(options =>
        options.JsonSerializerOptions.DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull);

// The same omit-nulls rule for envelopes written outside MVC's formatters — specifically the 401
// produced by ApiEnvelopeAuthorizationResultHandler, which is emitted by the authorization
// middleware before MVC is ever reached. Without this, that one response would carry a stray
// "data": null and diverge from every other error envelope.
builder.Services.ConfigureHttpJsonOptions(options =>
    options.SerializerOptions.DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull);

builder.Services.Configure<ApiBehaviorOptions>(options =>
{
    // Centralizes the 400 shape for both invalid ModelState (blank/missing required fields)
    // and malformed JSON (which [ApiController] also reports via ModelState) into the same
    // ApiResponse envelope used everywhere else (IP-01 §11) — not hand-built per endpoint.
    options.InvalidModelStateResponseFactory = context =>
    {
        var message = context.ModelState.Values
            .SelectMany(v => v.Errors)
            .Select(e => e.ErrorMessage)
            .FirstOrDefault(m => !string.IsNullOrWhiteSpace(m))
            ?? "The request is invalid.";

        return new BadRequestObjectResult(ApiResponse.Fail("VALIDATION_ERROR", message));
    };
});

// Learn more about configuring OpenAPI at https://aka.ms/aspnet/openapi
builder.Services.AddOpenApi();

var connectionString = builder.Configuration.GetConnectionString("DefaultConnection");
if (string.IsNullOrWhiteSpace(connectionString))
{
    throw new InvalidOperationException(
        "Connection string 'ConnectionStrings:DefaultConnection' is not configured. " +
        "Set it via user-secrets (dotnet user-secrets set \"ConnectionStrings:DefaultConnection\" \"...\" " +
        "--project src/WeaponDetection.Api) or the ConnectionStrings__DefaultConnection environment " +
        "variable. See README.md for local SQL Server setup.");
}

builder.Services.AddInfrastructure(connectionString, builder.Configuration);

// JWT Bearer validation + the AdminSession revocation check, applied to every endpoint that does
// not explicitly opt out with [AllowAnonymous] (IP-01 §12, T-10).
builder.Services.AddAdminAuthentication();

var app = builder.Build();

// Provisions the single AdminUser account (IP-01 §6) if none exists yet. Runs to completion
// before the app starts serving requests; an unhandled exception here (e.g. missing/invalid
// BootstrapAdmin configuration when no Admin exists) intentionally fails startup rather than
// running with no usable Admin account and no way to create one.
using (var startupScope = app.Services.CreateScope())
{
    var adminBootstrapper = startupScope.ServiceProvider.GetRequiredService<IAdminBootstrapper>();
    await adminBootstrapper.BootstrapAsync();
}

// Configure the HTTP request pipeline.
if (app.Environment.IsDevelopment())
{
    // The OpenAPI document describes the API surface rather than exposing its data, and is only
    // mapped in Development — it is exempted from the fallback authorization policy so the
    // document stays reachable from a browser/Swagger UI during local development.
    app.MapOpenApi().AllowAnonymous();
}

// HTTPS redirection intentionally omitted: ARCH-001 §9.1/§15.6 (ADR-002, amended) specifies
// HTTP-only for this trusted-LAN prototype; HTTPS is deferred to future hardening (ARCH-001 §28.2).
app.UseAuthentication();
app.UseAuthorization();

app.MapControllers();

app.Run();

// Exposes the top-level-statement-generated Program class to WebApplicationFactory<Program>
// in the integration test project (Microsoft.AspNetCore.Mvc.Testing requires a public type).
public partial class Program;
