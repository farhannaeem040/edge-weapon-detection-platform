import { AbstractControl, ValidationErrors, ValidatorFn } from '@angular/forms';

import {
  CAMERA_KEY_MAX_LENGTH,
  CAMERA_KEY_MIN_LENGTH,
  CAMERA_KEY_PATTERN,
  JETSON_HOST_MAX_LENGTH,
  RESERVED_CAMERA_KEYS,
  RTSP_OUTPUT_PORT_MAX,
  RTSP_OUTPUT_PORT_MIN,
} from './branch.models';

/**
 * The client-side mirrors of the Backend's branch-creation validation (IP-01 T-27; FS-02 §12).
 *
 * These exist for feedback, not for enforcement. The Backend re-validates every field it is sent and
 * remains authoritative (`CreateBranchRequestDto`'s DataAnnotations and `BranchService`'s RTSP
 * check); the point of duplicating the rules here is to spare the Admin a round trip for a mistake
 * the browser can already see. They are kept deliberately no stricter than the Backend's, because a
 * validator that rejects what the Backend would accept is a bug the Admin cannot work around.
 */

/**
 * Rejects a value that is empty or only whitespace, mirroring the Backend's `[NotBlank]`.
 *
 * Angular's own `Validators.required` treats `'   '` as present, which would let a whitespace-only
 * branch name through to a Backend that rejects it. This is the single "is there really a value
 * here" rule used by every text field in the form, and it reports under the standard `required` key
 * so a template needs only one error branch per field.
 */
export const notBlank: ValidatorFn = (control: AbstractControl): ValidationErrors | null => {
  const value: unknown = control.value;

  if (typeof value !== 'string' || value.trim().length === 0) {
    return { required: true };
  }

  return null;
};

/**
 * Accepts only an absolute `rtsp://` URL, mirroring `BranchService.EnsureValidRtspUrl` — the same
 * two conditions, in the same order: parseable as an absolute URI, and an `rtsp` scheme
 * (case-insensitive, as the Backend's `OrdinalIgnoreCase` comparison is). A relative path, an `http`
 * URL, or a bare hostname therefore fails here exactly as it would there.
 *
 * Blank input reports nothing: `notBlank` is what says "this field is required", and stacking a
 * second complaint on an empty box would tell the Admin their empty field is a malformed URL. This
 * mirrors the Backend, whose format check also defers to the presence check on blank input.
 *
 * The validator returns a bare flag and never the value. The error it feeds is rendered as a fixed
 * generic string ("Enter a valid RTSP URL."), because an RTSP URL may embed credentials in its
 * userinfo and an error message must not become the thing that leaks them (FS-02 §11).
 */
export const rtspUrl: ValidatorFn = (control: AbstractControl): ValidationErrors | null => {
  const value: unknown = control.value;

  if (typeof value !== 'string' || value.trim().length === 0) {
    return null;
  }

  let parsed: URL;
  try {
    // `new URL(x)` with no base parses absolute URLs only — a relative value throws, which is the
    // relative-URL rejection, not an unexpected failure.
    parsed = new URL(value.trim());
  } catch {
    return { rtspUrl: true };
  }

  // URL normalises the scheme to lower case and includes the colon.
  return parsed.protocol === 'rtsp:' ? null : { rtspUrl: true };
};

/**
 * Rejects a Device annotated-output base URL that the Backend's `Device` entity would refuse
 * (FS-11 §11). The Backend stays authoritative — this only spares the Admin a round trip.
 *
 * A base URL is host-and-port only: it must be absolute, use `rtsp`/`rtsps`, carry no embedded
 * credentials (which would leak into every composed output URL), and carry no path, query or
 * fragment (the per-camera path is appended to it, so a base that already had one would compose
 * into `.../cameras/x/cameras/y`).
 *
 * Blank is valid here: clearing the field is how an Admin unsets the base. Like `rtspUrl` above, the
 * validator returns a bare flag and never echoes the value.
 */
export const annotatedOutputBaseUrl: ValidatorFn = (
  control: AbstractControl,
): ValidationErrors | null => {
  const value: unknown = control.value;

  if (typeof value !== 'string' || value.trim().length === 0) {
    return null;
  }

  let parsed: URL;
  try {
    parsed = new URL(value.trim());
  } catch {
    return { annotatedOutputBaseUrl: true };
  }

  if (parsed.protocol !== 'rtsp:' && parsed.protocol !== 'rtsps:') {
    return { annotatedOutputBaseUrl: true };
  }

  if (parsed.hostname.length === 0) {
    return { annotatedOutputBaseUrl: true };
  }

  if (parsed.username.length > 0 || parsed.password.length > 0) {
    return { annotatedOutputBaseUrl: true };
  }

  if (parsed.search.length > 0 || parsed.hash.length > 0) {
    return { annotatedOutputBaseUrl: true };
  }

  // A trailing "/" is normalised away by the Backend, so it is accepted here.
  return parsed.pathname === '' || parsed.pathname === '/'
    ? null
    : { annotatedOutputBaseUrl: true };
};

/**
 * Rejects a CameraKey the Backend's `Camera.RequireCameraKey` would refuse (FS-12 §3).
 *
 * The pattern is mirrored byte-for-byte from the Backend constant rather than re-expressed, because
 * a client rule that is even slightly stricter becomes a mistake the Admin cannot work around.
 *
 * Distinct error keys are returned per cause so the template can explain *which* rule was broken —
 * "use lowercase" and "that key is reserved" need different fixes. The value is safe to echo (a key
 * is not a credential), but the messages stay fixed anyway, matching the file's existing style.
 *
 * Blank reports nothing: `notBlank` owns the "this field is required" message, and stacking a second
 * complaint on an empty box would tell the Admin their empty field is malformed.
 */
export const cameraKey: ValidatorFn = (control: AbstractControl): ValidationErrors | null => {
  const value: unknown = control.value;

  if (typeof value !== 'string' || value.trim().length === 0) {
    return null;
  }

  // Only surrounding whitespace is forgiven, exactly as the Backend trims. Interior whitespace is a
  // pattern violation, not something to strip — silently removing it would submit a key the Admin
  // never typed.
  const trimmed = value.trim();

  if (trimmed.length < CAMERA_KEY_MIN_LENGTH || trimmed.length > CAMERA_KEY_MAX_LENGTH) {
    return { cameraKeyLength: true };
  }

  if (RESERVED_CAMERA_KEYS.includes(trimmed)) {
    return { cameraKeyReserved: true };
  }

  return CAMERA_KEY_PATTERN.test(trimmed) ? null : { cameraKeyPattern: true };
};

/**
 * Rejects a Jetson host the Backend's `Device.RequireJetsonHost` would refuse (FS-12 §4).
 *
 * A host is *bare*: no scheme, no port, no path, no query, no fragment, no credentials. Each is
 * checked explicitly so the failure is attributable, rather than inferred from one opaque parse.
 *
 * A colon is legal only inside an IPv6 literal, which is how `host:8554` is caught while
 * `2001:db8::1` is accepted. IPv6 is supplied bare and bracketed by the Backend when it composes a
 * URL, so a pre-bracketed value is rejected here for the same reason it is there: two spellings of
 * one host would be two values meaning the same thing.
 */
export const jetsonHost: ValidatorFn = (control: AbstractControl): ValidationErrors | null => {
  const value: unknown = control.value;

  if (typeof value !== 'string' || value.trim().length === 0) {
    return null;
  }

  const trimmed = value.trim();

  if (trimmed.length > JETSON_HOST_MAX_LENGTH) {
    return { jetsonHostLength: true };
  }

  if (
    trimmed.includes('://') ||
    trimmed.includes('/') ||
    trimmed.includes('\\') ||
    trimmed.includes('@') ||
    trimmed.includes('?') ||
    trimmed.includes('#') ||
    trimmed.includes('[') ||
    trimmed.includes(']')
  ) {
    return { jetsonHost: true };
  }

  // Bare IPv6 is the only legitimate reason for a colon; anything else is an embedded port.
  const looksLikeIpv6 = trimmed.includes(':');
  if (looksLikeIpv6) {
    // Delegate the actual IPv6 grammar to the URL parser rather than hand-rolling it.
    try {
      const parsed = new URL(`rtsp://[${trimmed}]`);
      return parsed.hostname.length > 0 ? null : { jetsonHost: true };
    } catch {
      return { jetsonHost: true };
    }
  }

  if (/\s/.test(trimmed)) {
    return { jetsonHost: true };
  }

  try {
    const parsed = new URL(`rtsp://${trimmed}`);
    // `new URL` tolerates a trailing port/path silently; re-checking the parsed host against the
    // input is what catches anything the explicit tests above missed.
    return parsed.hostname === trimmed.toLowerCase() ? null : { jetsonHost: true };
  } catch {
    return { jetsonHost: true };
  }
};

/**
 * Rejects an RTSP output port outside the Backend's accepted range (FS-12 §4): an integer in
 * 1–65535. Zero, negatives, decimals and out-of-range values all fail.
 *
 * Blank reports nothing — `notBlank`/`required` owns presence, and the Backend applies its own 8554
 * default when the value is absent.
 */
export const rtspOutputPort: ValidatorFn = (control: AbstractControl): ValidationErrors | null => {
  const value: unknown = control.value;

  if (value === null || value === undefined || value === '') {
    return null;
  }

  // A number input still yields a string when the user types; both forms are judged identically.
  const parsed = typeof value === 'number' ? value : Number(value);

  if (!Number.isInteger(parsed)) {
    return { rtspOutputPort: true };
  }

  return parsed >= RTSP_OUTPUT_PORT_MIN && parsed <= RTSP_OUTPUT_PORT_MAX
    ? null
    : { rtspOutputPort: true };
};

/**
 * Flags duplicate CameraKeys across the cameras `FormArray` (FS-12 §3: unique within a Branch).
 *
 * Applied to the array rather than to each row, because uniqueness is a property of the set. Each
 * offending control is additionally marked with `cameraKeyDuplicate` so the message can appear
 * against the row that caused it rather than only at the top of the form.
 *
 * Scoped to *this* form only: the same key in another Branch is legitimate, so nothing here may
 * treat a key as globally unique.
 */
export const uniqueCameraKeys: ValidatorFn = (
  control: AbstractControl,
): ValidationErrors | null => {
  const rows = control as { controls?: AbstractControl[] };
  if (!Array.isArray(rows.controls)) {
    return null;
  }

  const seen = new Map<string, AbstractControl[]>();

  for (const row of rows.controls) {
    const keyControl = row.get('cameraKey');
    const raw: unknown = keyControl?.value;
    if (typeof raw !== 'string' || raw.trim().length === 0) {
      continue;
    }

    const key = raw.trim();
    seen.set(key, [...(seen.get(key) ?? []), keyControl!]);
  }

  let hasDuplicate = false;

  for (const [, controls] of seen) {
    const duplicated = controls.length > 1;
    hasDuplicate = hasDuplicate || duplicated;

    for (const keyControl of controls) {
      const existing = { ...(keyControl.errors ?? {}) };
      if (duplicated) {
        existing['cameraKeyDuplicate'] = true;
      } else {
        delete existing['cameraKeyDuplicate'];
      }

      keyControl.setErrors(Object.keys(existing).length > 0 ? existing : null, {
        emitEvent: false,
      });
    }
  }

  return hasDuplicate ? { cameraKeyDuplicate: true } : null;
};
