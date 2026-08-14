using System.Collections.Concurrent;
using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.IntegrationTests.Api;

// The host used by the live-monitoring HTTP-endpoint tests (FS-14 §5, IP-16 T-9). Its own named
// database, mirroring AlertSnapshotUploadApiFactory/AlertSnapshotRetrievalApiFactory. Unlike those,
// this factory also replaces the real IMediaGatewayClient with a recording stub — there is no real
// MediaMTX instance in the test environment, and recording the exact sourceUrl each test's request
// resolved to is what lets these tests prove the SSRF-protection contract (the client only ever sees
// { branchId, cameraId, mode }, yet the correct real RTSP source was resolved server-side) without
// that source ever appearing in an HTTP response body.
public sealed class LiveMonitoringApiFactory : SqlServerApiHostFactory
{
    public sealed class RecordingMediaGatewayClient : IMediaGatewayClient
    {
        public ConcurrentBag<(string PathName, string SourceUrl)> Calls { get; } = [];
        public bool ThrowOnEnsure { get; set; }

        public Task EnsurePathAsync(
            string pathName, string sourceUrl, CancellationToken cancellationToken = default)
        {
            if (ThrowOnEnsure)
            {
                throw new MediaGatewayException("simulated gateway failure");
            }

            Calls.Add((pathName, sourceUrl));
            return Task.CompletedTask;
        }
    }

    public RecordingMediaGatewayClient GatewayClient { get; } = new();

    public LiveMonitoringApiFactory()
        : base("WeaponDetectionLiveMonitoringApiTests")
    {
    }

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        base.ConfigureWebHost(builder);

        builder.ConfigureServices(services =>
        {
            services.RemoveAll<IMediaGatewayClient>();
            services.AddSingleton<IMediaGatewayClient>(GatewayClient);
        });
    }
}
