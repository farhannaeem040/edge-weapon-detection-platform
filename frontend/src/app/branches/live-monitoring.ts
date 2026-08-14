import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  OnDestroy,
  OnInit,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';

import { AuthService } from '../auth/auth.service';
import { LiveMonitoringCamera, LiveStreamMode } from './live-monitoring.models';
import { LiveMonitoringService } from './live-monitoring.service';

/** Query param names for the Live Monitoring tab's Camera/mode selection (FS-14 §5, IP-16 T-11/T-12). */
export const LIVE_MONITORING_CAMERA_PARAM = 'cameraId';
export const LIVE_MONITORING_MODE_PARAM = 'mode';

type ConnectionState = 'idle' | 'connecting' | 'connected' | 'unavailable' | 'error';

/**
 * The Branch Live Monitoring / Live Inference viewer (FS-14, IP-16 T-11). Hosted by
 * `branch-detail.ts`'s "Live Monitoring" tab; deep-linkable from `alert-detail.ts`'s "View Live
 * Camera"/"View Live Inference" actions via the same `cameraId`/`mode` query params this component
 * itself reads and writes (mirrors `alert-list.ts`'s query-param-driven filter pattern).
 *
 * Plays back over WebRTC via MediaMTX's WHEP endpoint using the browser's native
 * `RTCPeerConnection` — no external signaling library, matching FS-14 §5's "do not hand-roll
 * signalling MediaMTX already provides." The WHEP fetch is a raw `fetch()`, not `HttpClient`, so the
 * Admin JWT is attached to it manually (the `authInterceptor` only applies to `HttpClient` requests)
 * — this is what the Nginx `/media/` `auth_request` location expects (see nginx.conf).
 */
@Component({
  selector: 'app-live-monitoring',
  template: `
    <div class="live-monitoring">
      @if (cameras().length === 0 && !camerasLoading()) {
        <p class="live-monitoring__status status-text">No cameras are configured for this branch.</p>
      } @else {
        <div class="live-monitoring__controls">
          <label class="field">
            <span class="field__label">Camera</span>
            <select
              class="live-monitoring__camera-select"
              [disabled]="camerasLoading()"
              (change)="onCameraChange($event)"
            >
              @for (camera of cameras(); track camera.cameraId) {
                <option [value]="camera.cameraId" [selected]="camera.cameraId === selectedCameraId()">
                  {{ camera.name }}
                </option>
              }
            </select>
          </label>

          <div class="live-monitoring__mode field">
            <span class="field__label">Mode</span>
            <div class="live-monitoring__mode-buttons" role="group" aria-label="Playback mode">
              <button
                type="button"
                class="btn"
                [class.btn--primary]="selectedMode() === 'monitoring'"
                [class.btn--secondary]="selectedMode() !== 'monitoring'"
                [disabled]="!selectedCameraMonitoringAvailable()"
                (click)="onModeChange('monitoring')"
              >
                Live Monitoring
              </button>
              <button
                type="button"
                class="btn"
                [class.btn--primary]="selectedMode() === 'inference'"
                [class.btn--secondary]="selectedMode() !== 'inference'"
                [disabled]="!selectedCameraInferenceAvailable()"
                (click)="onModeChange('inference')"
              >
                Live Inference
              </button>
            </div>
          </div>
        </div>

        <div class="live-monitoring__player card">
          @if (connectionState() === 'unavailable') {
            <p class="live-monitoring__status banner banner--info" role="alert">
              This mode is not available for the selected Camera.
            </p>
          } @else if (connectionState() === 'error') {
            <p class="live-monitoring__status banner banner--error" role="alert">
              Stream unavailable. Try again.
            </p>
          } @else {
            @if (connectionState() === 'connecting') {
              <p class="live-monitoring__status status-text">
                <span class="spinner" aria-hidden="true"></span> Connecting…
              </p>
            }
            <video
              class="live-monitoring__video"
              [class.live-monitoring__video--hidden]="connectionState() !== 'connected'"
              #videoElement
              autoplay
              playsinline
              muted
            ></video>
          }
        </div>

        <p class="live-monitoring__connection-status">
          <span
            class="live-monitoring__dot"
            [class.live-monitoring__dot--connected]="connectionState() === 'connected'"
            [class.live-monitoring__dot--connecting]="connectionState() === 'connecting'"
            [class.live-monitoring__dot--error]="connectionState() === 'error' || connectionState() === 'unavailable'"
            aria-hidden="true"
          ></span>
          {{ connectionStatusLabel() }}
          @if (selectedCamera(); as camera) {
            — {{ camera.name }} — {{ selectedMode() === 'inference' ? 'Live Inference' : 'Live Monitoring' }}
          }
        </p>
      }
    </div>
  `,
  styles: `
    .live-monitoring__controls {
      display: flex;
      flex-wrap: wrap;
      gap: var(--space-4);
      margin-bottom: var(--space-4);
      align-items: flex-end;
    }

    .live-monitoring__mode-buttons {
      display: flex;
      gap: var(--space-2);
    }

    .live-monitoring__player {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 20rem;
      background: #0b0d10;
      padding: 0;
      overflow: hidden;
    }

    .live-monitoring__video {
      width: 100%;
      max-height: 32rem;
      display: block;
    }

    .live-monitoring__video--hidden {
      display: none;
    }

    .live-monitoring__status {
      color: var(--color-text-faint);
    }

    .live-monitoring__connection-status {
      display: flex;
      align-items: center;
      gap: var(--space-2);
      margin-top: var(--space-3);
      font-size: var(--text-sm);
      color: var(--color-text-muted);
    }

    .live-monitoring__dot {
      width: 0.6rem;
      height: 0.6rem;
      border-radius: 50%;
      background: var(--color-neutral-text, #6b7280);
      flex: none;
    }

    .live-monitoring__dot--connected {
      background: var(--color-success, #10441f);
    }

    .live-monitoring__dot--connecting {
      background: var(--color-warning, #4a3800);
    }

    .live-monitoring__dot--error {
      background: var(--color-danger, #ba1a1a);
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LiveMonitoringComponent implements OnInit, OnDestroy {
  readonly branchId = input.required<string>();

  private readonly liveMonitoringService = inject(LiveMonitoringService);
  private readonly authService = inject(AuthService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  protected readonly cameras = signal<LiveMonitoringCamera[]>([]);
  protected readonly camerasLoading = signal(true);
  protected readonly selectedCameraId = signal<string | null>(null);
  protected readonly selectedMode = signal<LiveStreamMode>('monitoring');
  protected readonly connectionState = signal<ConnectionState>('idle');

  private readonly videoElement = viewChild<ElementRef<HTMLVideoElement>>('videoElement');

  private peerConnection: RTCPeerConnection | null = null;
  private whepResourceUrl: string | null = null;
  private requestToken = 0;

  protected selectedCamera(): LiveMonitoringCamera | undefined {
    return this.cameras().find((c) => c.cameraId === this.selectedCameraId());
  }

  protected selectedCameraMonitoringAvailable(): boolean {
    return this.selectedCamera()?.monitoringAvailable ?? false;
  }

  protected selectedCameraInferenceAvailable(): boolean {
    return this.selectedCamera()?.inferenceAvailable ?? false;
  }

  protected connectionStatusLabel(): string {
    switch (this.connectionState()) {
      case 'connected':
        return 'Connected';
      case 'connecting':
        return 'Connecting';
      case 'unavailable':
        return 'Unavailable';
      case 'error':
        return 'Stream unavailable';
      default:
        return 'Idle';
    }
  }

  ngOnInit(): void {
    this.liveMonitoringService.listCameras(this.branchId()).subscribe({
      next: (cameras) => {
        this.camerasLoading.set(false);
        this.cameras.set(cameras);

        const params = this.route.snapshot.queryParamMap;
        const requestedCameraId = params.get(LIVE_MONITORING_CAMERA_PARAM);
        const requestedMode = params.get(LIVE_MONITORING_MODE_PARAM);

        const initialCamera =
          cameras.find((c) => c.cameraId === requestedCameraId) ?? cameras[0] ?? null;
        const initialMode: LiveStreamMode = requestedMode === 'inference' ? 'inference' : 'monitoring';

        if (initialCamera) {
          this.selectedCameraId.set(initialCamera.cameraId);
          this.selectedMode.set(initialMode);
          this.startPlayback();
        }
      },
      error: () => {
        this.camerasLoading.set(false);
      },
    });
  }

  ngOnDestroy(): void {
    this.stopPlayback();
  }

  protected onCameraChange(event: Event): void {
    const cameraId = (event.target as HTMLSelectElement).value;
    const camera = this.cameras().find((c) => c.cameraId === cameraId);
    if (!camera) {
      return;
    }

    // Never leave the previous Camera's video visible while the UI already labels the new one.
    this.stopPlayback();
    this.selectedCameraId.set(cameraId);

    const mode: LiveStreamMode =
      this.selectedMode() === 'inference' && camera.inferenceAvailable ? 'inference' : 'monitoring';
    this.selectedMode.set(mode);

    this.updateQueryParams(cameraId, mode);
    this.startPlayback();
  }

  protected onModeChange(mode: LiveStreamMode): void {
    if (mode === this.selectedMode()) {
      return;
    }

    this.stopPlayback();
    this.selectedMode.set(mode);

    const cameraId = this.selectedCameraId();
    if (cameraId) {
      this.updateQueryParams(cameraId, mode);
    }
    this.startPlayback();
  }

  private updateQueryParams(cameraId: string, mode: LiveStreamMode): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { [LIVE_MONITORING_CAMERA_PARAM]: cameraId, [LIVE_MONITORING_MODE_PARAM]: mode },
      queryParamsHandling: 'merge',
      replaceUrl: true,
    });
  }

  private startPlayback(): void {
    const cameraId = this.selectedCameraId();
    const camera = this.selectedCamera();
    const mode = this.selectedMode();
    if (!cameraId || !camera) {
      return;
    }

    const available = mode === 'inference' ? camera.inferenceAvailable : camera.monitoringAvailable;
    if (!available) {
      this.connectionState.set('unavailable');
      return;
    }

    const thisRequest = ++this.requestToken;
    this.connectionState.set('connecting');

    this.liveMonitoringService.createStream({ branchId: this.branchId(), cameraId, mode }).subscribe({
      next: (session) => {
        if (thisRequest !== this.requestToken) {
          return; // superseded by a later Camera/mode switch — never resurrect a stale player.
        }
        void this.connectWhep(session.playbackUrl, thisRequest);
      },
      error: () => {
        if (thisRequest === this.requestToken) {
          this.connectionState.set('error');
        }
      },
    });
  }

  private async connectWhep(playbackUrl: string, requestToken: number): Promise<void> {
    try {
      const pc = new RTCPeerConnection();
      pc.addTransceiver('video', { direction: 'recvonly' });
      pc.ontrack = (event) => {
        const video = this.videoElement()?.nativeElement;
        if (requestToken !== this.requestToken || !video) {
          return;
        }
        video.srcObject = event.streams[0] ?? null;
        this.connectionState.set('connected');
      };

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await waitForIceGatheringComplete(pc);

      const token = this.authService.getToken();
      const response = await fetch(playbackUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/sdp',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: pc.localDescription?.sdp ?? '',
      });

      if (requestToken !== this.requestToken) {
        pc.close();
        return;
      }

      if (!response.ok) {
        pc.close();
        this.connectionState.set('error');
        return;
      }

      const answerSdp = await response.text();
      await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });

      // Resolved against the page origin, not `playbackUrl` — `playbackUrl` is itself only a
      // relative path (never an absolute URL, see LiveStreamSession), so it cannot serve as a
      // `new URL()` base; MediaMTX's WHEP `Location` header may itself be relative or absolute.
      const location = response.headers.get('Location');
      this.whepResourceUrl = location ? new URL(location, window.location.origin).toString() : null;
      this.peerConnection = pc;
    } catch {
      if (requestToken === this.requestToken) {
        this.connectionState.set('error');
      }
    }
  }

  private stopPlayback(): void {
    this.requestToken++;
    this.connectionState.set('idle');

    const video = this.videoElement()?.nativeElement;
    if (video) {
      video.srcObject = null;
    }

    this.peerConnection?.close();
    this.peerConnection = null;

    if (this.whepResourceUrl) {
      const token = this.authService.getToken();
      // Best-effort session teardown — MediaMTX's own idle timeout cleans up regardless, so a
      // failed DELETE here is not surfaced as a player error.
      fetch(this.whepResourceUrl, {
        method: 'DELETE',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      }).catch(() => {});
      this.whepResourceUrl = null;
    }
  }
}

function waitForIceGatheringComplete(pc: RTCPeerConnection): Promise<void> {
  if (pc.iceGatheringState === 'complete') {
    return Promise.resolve();
  }

  return new Promise((resolve) => {
    const check = () => {
      if (pc.iceGatheringState === 'complete') {
        pc.removeEventListener('icegatheringstatechange', check);
        resolve();
      }
    };
    pc.addEventListener('icegatheringstatechange', check);
  });
}
