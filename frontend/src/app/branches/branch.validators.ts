import { AbstractControl, ValidationErrors, ValidatorFn } from '@angular/forms';

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
