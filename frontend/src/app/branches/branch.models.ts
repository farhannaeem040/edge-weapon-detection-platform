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
