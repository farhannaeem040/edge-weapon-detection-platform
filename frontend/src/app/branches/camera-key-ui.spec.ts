import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideHttpClient } from '@angular/common/http';
import { ComponentFixture, TestBed } from '@angular/core/testing';

import { environment } from '../../environments/environment';
import { Branch, DeviceNetworkUpdate } from './branch.models';
import { BranchService } from './branch.service';

/**
 * FS-12 / IP-14 T-284/T-288/T-289 — the frontend's side of the CameraKey and Jetson-network contract.
 *
 * These assert the two separations the feature exists to create: the public key is what an operator
 * is shown, and the Device's advertised address is a client-facing concern that never implies
 * anything about the pipeline.
 */
describe('FS-12 branch service network contract', () => {
  let service: BranchService;
  let httpTesting: HttpTestingController;

  const devicesUrl = `${environment.apiBaseUrl}/devices`;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [BranchService, provideHttpClient(), provideHttpClientTesting()],
    });

    service = TestBed.inject(BranchService);
    httpTesting = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpTesting.verify());

  it('calls the FS-12 network route, not the superseded base-url route', () => {
    service.setDeviceNetwork('b1', '192.168.1.50', 9554).subscribe();

    const request = httpTesting.expectOne(`${devicesUrl}/b1/network`);
    expect(request.request.method).toBe('PUT');
    request.flush({ success: true, data: { branchId: 'b1' } });
  });

  it('sends the host and port as two separate fields', () => {
    service.setDeviceNetwork('b1', '192.168.1.50', 9554).subscribe();

    const request = httpTesting.expectOne(`${devicesUrl}/b1/network`);
    expect(request.request.body).toEqual({ jetsonHost: '192.168.1.50', rtspOutputPort: 9554 });
    request.flush({ success: true, data: { branchId: 'b1' } });
  });

  it('sends nulls to clear the configuration', () => {
    service.setDeviceNetwork('b1', null, null).subscribe();

    const request = httpTesting.expectOne(`${devicesUrl}/b1/network`);
    expect(request.request.body).toEqual({ jetsonHost: null, rtspOutputPort: null });
    request.flush({ success: true, data: { branchId: 'b1' } });
  });

  it('returns the Backend-computed base rather than composing one', () => {
    let update: DeviceNetworkUpdate | null = null;
    service.setDeviceNetwork('b1', '2001:db8::1', 8554).subscribe((result) => (update = result));

    httpTesting.expectOne(`${devicesUrl}/b1/network`).flush({
      success: true,
      // IPv6 bracketing is the Backend's job; the client must not reproduce it.
      data: {
        branchId: 'b1',
        jetsonHost: '2001:db8::1',
        rtspOutputPort: 8554,
        annotatedOutputBaseUrl: 'rtsp://[2001:db8::1]:8554',
      },
    });

    expect(update!.annotatedOutputBaseUrl).toBe('rtsp://[2001:db8::1]:8554');
  });

  it('maps a not-found branch to null rather than throwing', () => {
    let update: DeviceNetworkUpdate | null | undefined;
    service.setDeviceNetwork('missing', '10.0.0.1', 8554).subscribe((r) => (update = r));

    httpTesting
      .expectOne(`${devicesUrl}/missing/network`)
      .flush({ success: false }, { status: 404, statusText: 'Not Found' });

    expect(update).toBeNull();
  });
});

/**
 * The read model carries the key alongside — never instead of — the immutable id, and tolerates a
 * Device that predates the FS-12 migration (task Phase 10).
 */
describe('FS-12 transitional and identity handling', () => {
  function branch(overrides: Partial<Branch['device']> = {}): Branch {
    return {
      branchId: 'b1',
      name: 'Ljmu Branch',
      address: '1 High Street',
      contactDetails: 'ops@example.local',
      cameras: [
        {
          cameraId: '2613b331-8783-4d51-903a-3e41a979a14c',
          cameraKey: 'front-camera',
          name: 'Front Camera',
          rtspUrl: 'rtsp://100.77.146.5:8554/camera1',
          enabled: true,
          sourceOrder: 0,
          outputPath: 'cameras/front-camera',
          outputStreamUrl: 'rtsp://100.98.226.80:8554/cameras/front-camera',
        },
      ],
      device: { activationStatus: 'Activated', ...overrides },
    };
  }

  it('keeps the immutable CameraId distinct from the public CameraKey', () => {
    const camera = branch().cameras[0];

    expect(camera.cameraId).toBe('2613b331-8783-4d51-903a-3e41a979a14c');
    expect(camera.cameraKey).toBe('front-camera');
    expect(camera.cameraId).not.toBe(camera.cameraKey);
  });

  it('derives the mount from the key, not the id', () => {
    const camera = branch().cameras[0];

    expect(camera.outputPath).toBe(`cameras/${camera.cameraKey}`);
    expect(camera.outputPath).not.toContain(camera.cameraId);
  });

  it('prefers the Backend-provided outputStreamUrl over anything client-composed', () => {
    const camera = branch({ jetsonHost: '10.0.0.9', rtspOutputPort: 1234 }).cameras[0];

    // The Backend's value wins even when host/port would compose something different — the client
    // never assembles the URL itself.
    expect(camera.outputStreamUrl).toBe('rtsp://100.98.226.80:8554/cameras/front-camera');
  });

  it('models a pre-migration Device with no structured network fields', () => {
    // Option A retains the legacy base URL, so a Device that has not been migrated still carries a
    // usable base while jetsonHost/rtspOutputPort are absent.
    const device = branch({ annotatedOutputBaseUrl: 'rtsp://100.98.226.80:8554' }).device;

    expect(device.jetsonHost).toBeUndefined();
    expect(device.rtspOutputPort).toBeUndefined();
    expect(device.annotatedOutputBaseUrl).toBe('rtsp://100.98.226.80:8554');
  });

  it('models an entirely unconfigured Device without inventing an address', () => {
    const device = branch().device;

    expect(device.jetsonHost).toBeUndefined();
    expect(device.annotatedOutputBaseUrl).toBeUndefined();
  });
});
