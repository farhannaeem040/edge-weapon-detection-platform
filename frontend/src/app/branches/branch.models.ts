/**
 * The read-side wire contract of the Branch endpoints (IP-01 T-16/T-26; FS-02 §10.3).
 *
 * These interfaces are transcribed from the Backend's `BranchResponseDto`/`CameraResponseDto`/
 * `DeviceSummaryDto` and the shapes asserted by `BranchApiTests`, not invented here. Two of the
 * Backend's disclosure rules are visible in the types themselves and must stay that way:
 *
 *  - `activationKey` is **absent**. The Backend puts the plaintext key on the wire only in the
 *    create response, exactly once (FS-02 §5.4, §10.1), and T-26 implements no create path. Not
 *    modelling it is what guarantees no read view can render or retain it.
 *  - The internal `DeviceRecordId`, activation-key hashes, and the `ProtectedSharedSecret` are not
 *    members of any Backend read DTO, so there is nothing here to mirror — and nothing to add.
 */

/** A camera as returned by the Backend (`CameraResponseDto`). */
export interface Camera {
  /**
   * The immutable internal identity (`Camera.CameraId`). This is what every DetectionEvent and Alert
   * correlates on and what the Agent maps `source_id` to — never editable, never replaced by
   * `cameraKey` (FS-12 §2). Kept for `track` identity and technical diagnostics; it is *not* the
   * identifier an operator is shown as the stream's name.
   */
  cameraId: string;

  /**
   * The administrator-defined **public** stream identifier (FS-12 §2). This is the segment that
   * appears in the annotated RTSP mount (`cameras/front-entrance`), so it is what an operator reads,
   * types and communicates. Immutable after creation and unique within its Branch — the key names
   * the *stream*, the GUID names the *Camera*.
   */
  cameraKey: string;

  name: string;

  /**
   * The camera's RTSP URL **as the Backend returned it**. The stored value may embed credentials in
   * its userinfo component; the Backend replaces that span with `***` before the value leaves it
   * (`RtspUrlSanitizer`). The Dashboard therefore treats this as an opaque display string and does
   * no redaction of its own: re-redacting client-side would imply the raw value had reached the
   * browser, and reconstructing a full URL from it is impossible by design (ARCH-001 §15.6).
   */
  rtspUrl: string;

  enabled: boolean;

  /** This camera's DeepStream source index, assigned by the Backend (FS-11 §2). */
  sourceOrder: number;

  /**
   * The **relative** RTSP mount this camera's *annotated* (bounding-box overlaid) stream is
   * published on by its Device — `cameras/front-entrance` (FS-11 §11, FS-12 §2.1). Derived by the
   * Backend from the immutable `cameraKey`, so it is stable across a rename and across an `rtspUrl`
   * change alike. This is an output identity and is never the camera's input URL.
   */
  outputPath: string;

  /**
   * The complete, client-usable annotated output URL — `device.annotatedOutputBaseUrl` joined to
   * `outputPath`. The Backend leaves it **null** when the Device has no output base configured
   * (rather than guessing an address from the request host), and its `WhenWritingNull` policy then
   * drops the member entirely — hence the optional marker. A view must render the "not configured"
   * state rather than fabricate a URL.
   *
   * This is an RTSP URL: it is displayed and copyable, but a browser cannot play it natively.
   * Browser playback (HLS/WebRTC) is a separate future feature.
   */
  outputStreamUrl?: string;
}

/**
 * The states of a Device, spelled exactly as the Backend serializes `DeviceActivationStatus` (its
 * enum name, not an integer — `DeviceSummaryDto` emits `ActivationStatus.ToString()`) — FS-02 §10.3.
 *
 * `ReactivationRequired` (IP-05, FS-02 §5.3 amended) is entered when an Admin regenerates the
 * Activation Key of an already-`Activated` device: the Backend revokes the shared secret immediately
 * and preserves the `DeviceId` (§4.1/§4.2). It is a **credential-revocation** state, not a
 * connectivity state — the Dashboard must render it as "Reactivation required" and must **never**
 * show it (or any device) as *Offline*, which stays reserved for a future heartbeat feature that this
 * platform does not implement (IP-05 §13).
 */
export type DeviceActivationStatus = 'Unactivated' | 'Activated' | 'ReactivationRequired';

/** The Device summarised within a branch (`DeviceSummaryDto`). */
export interface DeviceSummary {
  /**
   * The Jetson's reachable IP address or hostname (FS-12 §4). Deliberately *not* named for
   * Tailscale: the POC's Tailscale IP is only one of the values this field legitimately carries,
   * alongside LAN IPs, routed private IPs, public IPs and DNS hostnames.
   *
   * Omitted by the Backend until an Admin configures it, and still absent for a Device that predates
   * the FS-12 migration — a view must render "not configured" rather than invent an address.
   */
  jetsonHost?: string;

  /** The Jetson's RTSP output port (FS-12 §4); 8554 by default. Omitted until configured. */
  rtspOutputPort?: number;

  /**
   * The public Device ID. Present only once the Device is activated — the Backend leaves it null
   * before then and its `WhenWritingNull` policy drops the member from the payload entirely, hence
   * the optional marker (FS-02 §10.3, AC-7).
   *
   * This is *not* how activation state is determined. `activationStatus` is the explicit field and
   * the only thing a view may branch on.
   */
  deviceId?: string;

  activationStatus: DeviceActivationStatus;

  /** Omitted by the Backend until the Device has reported an address. */
  lastKnownAddress?: string;

  /**
   * The externally reachable base of this Device's annotated-output RTSP server, e.g.
   * `rtsp://100.98.226.80:8554`. Under FS-12 this is **computed** by the Backend from
   * `jetsonHost`/`rtspOutputPort` (falling back to the retained legacy column during the transition),
   * not independently stored — the client displays it rather than composing it, so IPv6 bracketing
   * stays server-side. It is **not** a credential and not a camera input URL. Omitted until the
   * Device's network is configured.
   */
  annotatedOutputBaseUrl?: string;
}

/**
 * Request body of `PUT /api/v1/devices/{branchId}/network` (backend `SetDeviceNetworkRequestDto`) —
 * FS-12 §4. Replaces the former `annotated-output-base-url` route: host and port are two independent
 * facts, and the composed base is now derived from them rather than being a URL string the client
 * has to assemble correctly.
 *
 * A `null` host clears the configuration (and the port with it), returning every camera's
 * `outputStreamUrl` to null. The Backend's `Device` entity remains authoritative for every format
 * rule; the client-side validators only spare a round trip.
 */
export interface SetDeviceNetworkRequest {
  jetsonHost: string | null;
  rtspOutputPort: number | null;
}

/** `data` of a successful Device network update (backend `SetDeviceNetworkResponseDto`). */
export interface DeviceNetworkUpdate {
  branchId: string;
  deviceId?: string;
  jetsonHost?: string;
  rtspOutputPort?: number;
  /** The **computed** base, echoed so the client can show exactly what URLs now compose to. */
  annotatedOutputBaseUrl?: string;
}

/** Mirrors `Device.JetsonHostMaxLength`. */
export const JETSON_HOST_MAX_LENGTH = 255;

/** Mirrors `Device.DefaultRtspOutputPort`. */
export const DEFAULT_RTSP_OUTPUT_PORT = 8554;

export const RTSP_OUTPUT_PORT_MIN = 1;
export const RTSP_OUTPUT_PORT_MAX = 65535;

/** A branch as returned by `GET /api/v1/branches` and `GET /api/v1/branches/{id}`. */
export interface Branch {
  branchId: string;
  name: string;
  address: string;
  contactDetails: string;
  cameras: Camera[];
  device: DeviceSummary;
}

/**
 * The write-side wire contract of `POST /api/v1/branches` (IP-01 T-27; FS-02 §10.1).
 *
 * Transcribed field-for-field from the Backend's `CreateBranchRequestDto`/`CameraConfigDto`. What is
 * *absent* is as deliberate as what is present: the Backend's create DTO accepts a name, an address,
 * contact details and cameras, and nothing else. No client-generated `branchId`/`cameraId`, no
 * device id, no activation status, no activation key, and no `enabled` flag — enablement is not a
 * creation input (FS-02 §9) and the Backend would ignore any of these, so modelling them here would
 * invent a contract that does not exist.
 */
export interface CreateCameraRequest {
  name: string;
  rtspUrl: string;

  /**
   * The administrator-entered public stream key (FS-12 §3). Required, and deliberately never
   * generated from `name` at submission time — a key derived from a mutable label would reintroduce
   * the coupling FS-11/FS-12 removed. `sourceOrder` and `enabled` stay absent: order is still
   * assigned by the Backend from array position, and enablement is still not a creation input.
   */
  cameraKey: string;
}

/** Request body of `POST /api/v1/branches` (backend `CreateBranchRequestDto`). */
export interface CreateBranchRequest {
  name: string;
  address: string;
  contactDetails: string;

  /**
   * The reserved Device's reachable address (FS-12 §4). Required at branch creation: that Device is
   * the RTSP host for every one of the Branch's annotated Camera outputs.
   */
  jetsonHost: string;

  /** Optional on the wire — the Backend applies its 8554 default when this is null. */
  rtspOutputPort: number | null;

  /** At least one camera is required at branch creation (FS-02 §12). */
  cameras: CreateCameraRequest[];
}

/**
 * `data` of a successful `POST /api/v1/branches` (backend `BranchResponseDto.ForCreate`).
 *
 * This is the one and only response shape that carries `activationKey` — the complete plaintext key
 * (`keyId.secret`), disclosed exactly once at generation time (FS-02 §5.1 step 7, §10.1). It is
 * required here, not optional, because a create response without it is a contract violation the
 * service rejects rather than a state any view should try to render.
 *
 * The key is never modelled on `Branch` itself, so no read view can ever hold or render one.
 */
export interface CreatedBranch extends Branch {
  activationKey: string;
}

/**
 * The write-side wire contract of `PUT /api/v1/branches/{branchId}` (IP-03 T-43; FS-03 §10.1).
 *
 * Transcribed field-for-field from the Backend's `UpdateBranchRequestDto`/`UpdateCameraDto`. It is
 * deliberately close to `CreateBranchRequest`, with one addition and nothing else: each camera
 * carries an optional `cameraId`.
 *
 * That single optional id is the whole identity contract of an edit (FS-03 §1.3, §5.2):
 *
 *  - a camera **with** a `cameraId` is an existing camera being updated in place — its id, and the
 *    device/activation state hanging off the branch, are preserved;
 *  - a camera **without** one is a new camera, for which the Backend generates a fresh identity on
 *    add;
 *  - an existing camera the request omits entirely is removed.
 *
 * The `cameraId` is the same public identifier the read DTOs already return on `Camera`; no new
 * identifier is introduced. What stays absent is as deliberate as in the create contract: no
 * `branchId` in the body (it is the route), no device field, no activation state, no activation key,
 * no `enabled` flag, no `DeviceRecordId`, and no secret — the Backend accepts none of them on an
 * edit, and editing must never touch device identity, activation, or key state (FS-03 §5.4, §12).
 */
export interface UpdateCameraRequest {
  /** The existing camera's public id when editing it in place; omitted when adding a new camera. */
  cameraId?: string;
  name: string;
  rtspUrl: string;

  /**
   * FS-12 §3. **Required when adding** a camera (no `cameraId`); **omitted when editing** one.
   *
   * Omitting it on an edit is deliberate rather than incidental: the Backend rejects a key that
   * differs from the stored value with `CAMERA_KEY_IMMUTABLE`, so echoing an unchanged key back
   * would add a way to fail without adding any capability. Not sending it at all is the contract
   * that cannot accidentally attempt a rename.
   */
  cameraKey?: string;
}

/** Request body of `PUT /api/v1/branches/{branchId}` (backend `UpdateBranchRequestDto`). */
export interface UpdateBranchRequest {
  name: string;
  address: string;
  contactDetails: string;

  /** At least one camera must remain after an edit (FS-03 §5.2). */
  cameras: UpdateCameraRequest[];
}

/**
 * `data` of a successful `POST /api/v1/devices/{branchId}/activation-key/regenerate` (backend
 * `RegenerateActivationKeyResponseDto`) — IP-01 T-28; FS-02 §5.3 step 5, §10.2.
 *
 * Transcribed field-for-field from that DTO, which is a record of exactly one member. Alongside
 * `CreatedBranch.activationKey` this is the *only* other shape that ever carries the plaintext key,
 * and it carries nothing else: no old key, no key hash, no key status, no `DeviceRecordId`, no
 * protected or device shared secret. The Backend's DTO has no member for any of them, so there is
 * nothing here to mirror — and nothing to add.
 */
export interface RegeneratedActivationKey {
  /** The new complete plaintext key (`keyId.secret`), disclosed in this response alone. */
  activationKey: string;
}

/**
 * The Backend's own length limits, mirrored so client-side feedback agrees with the DataAnnotations
 * that will judge the submission (`Branch.*MaxLength`, `Camera.*MaxLength`). The Backend remains
 * authoritative; these only spare the Admin a round trip.
 */
export const BRANCH_NAME_MAX_LENGTH = 200;
export const BRANCH_ADDRESS_MAX_LENGTH = 500;
export const BRANCH_CONTACT_DETAILS_MAX_LENGTH = 500;
export const CAMERA_NAME_MAX_LENGTH = 200;
export const CAMERA_RTSP_URL_MAX_LENGTH = 2048;

/** Mirrors `Camera.CameraKeyMinLength`/`CameraKeyMaxLength` (FS-12 §3). */
export const CAMERA_KEY_MIN_LENGTH = 3;
export const CAMERA_KEY_MAX_LENGTH = 64;

/**
 * The frozen FS-12 §3 key grammar, mirrored from `Camera.CameraKeyPattern`. Kept byte-identical to
 * the Backend's regex so the browser never rejects what the Backend would accept, nor vice versa.
 */
export const CAMERA_KEY_PATTERN = /^[a-z0-9](?:[a-z0-9-]{1,62}[a-z0-9])$/;

/** Mirrors `Camera.ReservedCameraKeys` (FS-12 §3). */
export const RESERVED_CAMERA_KEYS: readonly string[] = [
  'ds-test',
  'api',
  'admin',
  'health',
  'metrics',
  'cameras',
];
