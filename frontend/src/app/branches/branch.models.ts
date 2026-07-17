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
  cameraId: string;
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
}

/**
 * The two states of a Device, spelled exactly as the Backend serializes
 * `DeviceActivationStatus` (its enum name, not an integer) — FS-02 §10.3.
 */
export type DeviceActivationStatus = 'Unactivated' | 'Activated';

/** The Device summarised within a branch (`DeviceSummaryDto`). */
export interface DeviceSummary {
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
}

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
}

/** Request body of `POST /api/v1/branches` (backend `CreateBranchRequestDto`). */
export interface CreateBranchRequest {
  name: string;
  address: string;
  contactDetails: string;

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
