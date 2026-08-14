import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';

import { environment } from '../../environments/environment';
import { AuthService } from '../auth/auth.service';
import { LiveMonitoringComponent } from './live-monitoring';
import { LiveMonitoringCamera } from './live-monitoring.models';

const BRANCH_ID = '11111111-1111-1111-1111-111111111111';
const CAMERA_A = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
const CAMERA_B = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
const CAMERAS_URL = `${environment.apiBaseUrl}/branches/${BRANCH_ID}/live-monitoring/cameras`;
const STREAMS_URL = `${environment.apiBaseUrl}/live-streams`;

/** A minimal fake standing in for the real WebRTC RTCPeerConnection (FS-14 §5, IP-16 T-14) — there
 * is no real media gateway in this test environment, so this only proves the component's own
 * lifecycle (one active connection at a time, closed before a new one starts, closed on destroy). */
class FakePeerConnection {
  static instances: FakePeerConnection[] = [];
  closed = false;
  iceGatheringState: RTCIceGatheringState = 'complete';
  ontrack: ((event: Partial<RTCTrackEvent>) => void) | null = null;

  constructor() {
    FakePeerConnection.instances.push(this);
  }

  addTransceiver(): void {}
  addEventListener(): void {}
  removeEventListener(): void {}
  createOffer(): Promise<RTCSessionDescriptionInit> {
    return Promise.resolve({ type: 'offer', sdp: 'fake-offer-sdp' });
  }
  setLocalDescription(desc: RTCSessionDescriptionInit): Promise<void> {
    (this as unknown as { localDescription: RTCSessionDescriptionInit }).localDescription = desc;
    return Promise.resolve();
  }
  setRemoteDescription(): Promise<void> {
    return Promise.resolve();
  }
  close(): void {
    this.closed = true;
  }
}

describe('LiveMonitoringComponent', () => {
  let fixture: ComponentFixture<LiveMonitoringComponent>;
  let httpTesting: HttpTestingController;
  let fetchSpy: jasmine.Spy;
  let originalRtcPeerConnection: typeof RTCPeerConnection;

  const cameras: LiveMonitoringCamera[] = [
    { cameraId: CAMERA_A, name: 'Front Camera', cameraKey: 'front-camera', monitoringAvailable: true, inferenceAvailable: true },
    { cameraId: CAMERA_B, name: 'Rear Entrance', cameraKey: 'rear-entrance', monitoringAvailable: true, inferenceAvailable: false },
  ];

  beforeEach(async () => {
    TestBed.resetTestingModule();

    originalRtcPeerConnection = window.RTCPeerConnection;
    (window as unknown as { RTCPeerConnection: unknown }).RTCPeerConnection = FakePeerConnection;
    FakePeerConnection.instances = [];

    // A fresh Response per call — a Response body can only be read once, and more than one test
    // here triggers more than one fetch (mode/Camera switches each start a new WHEP connect).
    fetchSpy = spyOn(window, 'fetch').and.callFake(() =>
      Promise.resolve(
        new Response('fake-answer-sdp', { status: 201, headers: { Location: '/media/x/whep/session-1' } }),
      ),
    );

    await TestBed.configureTestingModule({
      imports: [LiveMonitoringComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { queryParamMap: convertToParamMap({}) } },
        },
        { provide: AuthService, useValue: { getToken: () => 'fake-jwt' } },
      ],
    }).compileComponents();

    httpTesting = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(LiveMonitoringComponent);
    fixture.componentRef.setInput('branchId', BRANCH_ID);
  });

  afterEach(() => {
    window.RTCPeerConnection = originalRtcPeerConnection;
    httpTesting.verify();
  });

  function element(): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  function flushCameraList(): void {
    fixture.detectChanges();
    httpTesting.expectOne(CAMERAS_URL).flush({ success: true, data: cameras });
    fixture.detectChanges();
  }

  function flushStreamCreate(cameraId: string, mode: string, playbackUrl = '/media/x/whep'): void {
    const request = httpTesting.expectOne(STREAMS_URL);
    expect(request.request.body).toEqual({ branchId: BRANCH_ID, cameraId, mode });
    request.flush({
      success: true,
      data: { sessionId: 's1', cameraId, mode, playbackUrl, expiresAtUtc: '2026-01-01T00:00:00Z' },
    });
    fixture.detectChanges();
  }

  // zone.js's default test bundle does not patch the global `fetch`, so `fixture.whenStable()`
  // cannot be relied on to wait for the WHEP connect promise chain — this drains microtasks
  // explicitly instead, enough times to cover every `await` in `connectWhep`.
  async function flushMicrotasks(): Promise<void> {
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
  }

  it('loads only this branch\'s cameras', () => {
    flushCameraList();
    flushStreamCreate(CAMERA_A, 'monitoring');

    expect(element().textContent).toContain('Front Camera');
    expect(element().textContent).toContain('Rear Entrance');
  });

  it('defaults to the first camera and monitoring mode, and requests a stream', async () => {
    flushCameraList();
    flushStreamCreate(CAMERA_A, 'monitoring');
    await flushMicrotasks();

    expect(fetchSpy).toHaveBeenCalled();
  });

  it('disables the Live Inference button when the selected camera has no inference output', () => {
    flushCameraList();
    flushStreamCreate(CAMERA_A, 'monitoring');

    // Switch to the camera with inferenceAvailable = false.
    const select = element().querySelector('select')!;
    select.value = CAMERA_B;
    select.dispatchEvent(new Event('change'));
    fixture.detectChanges();
    flushStreamCreate(CAMERA_B, 'monitoring');

    const inferenceButton = Array.from(element().querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Live Inference'),
    ) as HTMLButtonElement;
    expect(inferenceButton.disabled).toBeTrue();
  });

  it('starts a fresh RTCPeerConnection and clears the previous video when switching Camera', async () => {
    flushCameraList();
    flushStreamCreate(CAMERA_A, 'monitoring');
    await flushMicrotasks();
    FakePeerConnection.instances[0].ontrack?.({ streams: [new MediaStream()] });
    fixture.detectChanges();
    const video = element().querySelector<HTMLVideoElement>('video')!;
    expect(video.srcObject).not.toBeNull();

    const select = element().querySelector('select')!;
    select.value = CAMERA_B;
    select.dispatchEvent(new Event('change'));
    fixture.detectChanges();

    // Never leaves the old Camera's video visible while the UI already labels the new one.
    expect(video.srcObject).toBeNull();
    flushStreamCreate(CAMERA_B, 'monitoring');
    await flushMicrotasks();

    expect(FakePeerConnection.instances.length).toBe(2);
  });

  it('starts a fresh RTCPeerConnection and clears the previous video when switching mode', async () => {
    flushCameraList();
    flushStreamCreate(CAMERA_A, 'monitoring');
    await flushMicrotasks();
    FakePeerConnection.instances[0].ontrack?.({ streams: [new MediaStream()] });
    fixture.detectChanges();
    const video = element().querySelector<HTMLVideoElement>('video')!;
    expect(video.srcObject).not.toBeNull();

    const inferenceButton = Array.from(element().querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Live Inference'),
    ) as HTMLButtonElement;
    inferenceButton.click();
    fixture.detectChanges();

    expect(video.srcObject).toBeNull();
    flushStreamCreate(CAMERA_A, 'inference');
    await flushMicrotasks();

    expect(FakePeerConnection.instances.length).toBe(2);
  });

  it('shows the connected state once the WHEP handshake completes', async () => {
    flushCameraList();
    flushStreamCreate(CAMERA_A, 'monitoring');
    await flushMicrotasks();

    const connection = FakePeerConnection.instances[0];
    connection.ontrack?.({ streams: [new MediaStream()] });
    fixture.detectChanges();

    expect(element().textContent).toContain('Connected');
  });

  it('shows an error state when the stream request fails', () => {
    flushCameraList();
    const request = httpTesting.expectOne(STREAMS_URL);
    request.flush({ success: false }, { status: 502, statusText: 'Bad Gateway' });
    fixture.detectChanges();

    expect(element().textContent).toContain('Stream unavailable');
  });

  it('closes the RTCPeerConnection and issues a best-effort DELETE on destroy', async () => {
    flushCameraList();
    flushStreamCreate(CAMERA_A, 'monitoring');
    // The connect promise chain (createOffer/setLocalDescription/fetch/setRemoteDescription) must
    // fully settle — including the real Response body read — before destroy can have anything to
    // clean up; a single macrotask tick reliably drains it in every browser's task scheduler.
    await new Promise<void>((resolve) => setTimeout(resolve, 50));
    const connection = FakePeerConnection.instances[0];
    fetchSpy.calls.reset();

    fixture.destroy();

    expect(connection.closed).toBeTrue();
    // Resolved to an absolute URL against the page origin (see live-monitoring.ts's connectWhep) —
    // MediaMTX's WHEP Location header may itself be relative or absolute.
    expect(fetchSpy).toHaveBeenCalledWith(
      `${window.location.origin}/media/x/whep/session-1`,
      jasmine.objectContaining({ method: 'DELETE' }),
    );
  });
});
