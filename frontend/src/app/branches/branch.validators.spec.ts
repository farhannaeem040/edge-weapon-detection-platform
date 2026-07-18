import { FormControl } from '@angular/forms';

import { notBlank, rtspUrl } from './branch.validators';

// Synthetic values only: every host below is a reserved `.invalid` name.
describe('branch validators', () => {
  describe('notBlank', () => {
    it('accepts a value with content', () => {
      expect(notBlank(new FormControl('Placeholder Branch'))).toBeNull();
    });

    it('rejects an empty value', () => {
      expect(notBlank(new FormControl(''))).toEqual({ required: true });
    });

    it('rejects a whitespace-only value', () => {
      // The gap Validators.required leaves, and the Backend's [NotBlank] closes.
      expect(notBlank(new FormControl('   '))).toEqual({ required: true });
      expect(notBlank(new FormControl('\t\n '))).toEqual({ required: true });
    });

    it('rejects a null value', () => {
      expect(notBlank(new FormControl(null))).toEqual({ required: true });
    });

    it('accepts a value padded with whitespace', () => {
      // The form trims before submitting, so this is a valid "  A  ", not a blank.
      expect(notBlank(new FormControl('  A  '))).toBeNull();
    });
  });

  describe('rtspUrl', () => {
    it('accepts an absolute rtsp URL', () => {
      expect(rtspUrl(new FormControl('rtsp://camera.example.invalid:554/stream1'))).toBeNull();
    });

    it('accepts an rtsp URL regardless of scheme case', () => {
      // The Backend compares the scheme with OrdinalIgnoreCase; this validator must not be stricter.
      expect(rtspUrl(new FormControl('RTSP://camera.example.invalid/stream1'))).toBeNull();
    });

    it('accepts an rtsp URL padded with whitespace', () => {
      expect(rtspUrl(new FormControl('  rtsp://camera.example.invalid/stream1  '))).toBeNull();
    });

    it('rejects a non-rtsp scheme', () => {
      expect(rtspUrl(new FormControl('http://camera.example.invalid/stream1'))).toEqual({
        rtspUrl: true,
      });
      expect(rtspUrl(new FormControl('https://camera.example.invalid/stream1'))).toEqual({
        rtspUrl: true,
      });
      expect(rtspUrl(new FormControl('file:///etc/passwd'))).toEqual({ rtspUrl: true });
    });

    it('rejects a relative URL', () => {
      expect(rtspUrl(new FormControl('/streams/stream1'))).toEqual({ rtspUrl: true });
      expect(rtspUrl(new FormControl('camera.example.invalid/stream1'))).toEqual({ rtspUrl: true });
    });

    it('reports nothing for a blank value', () => {
      // notBlank owns "this is required"; complaining twice about one empty box helps nobody, and
      // the Backend's format check defers on blank input in exactly the same way.
      expect(rtspUrl(new FormControl(''))).toBeNull();
      expect(rtspUrl(new FormControl('   '))).toBeNull();
      expect(rtspUrl(new FormControl(null))).toBeNull();
    });

    it('returns a bare flag that never carries the submitted value', () => {
      const credentialBearingUrl = 'http://someuser:somepass@camera.example.invalid/stream1';

      const errors = rtspUrl(new FormControl(credentialBearingUrl));

      // The error feeds a fixed generic message; nothing derived from the URL may travel with it
      // (FS-02 §11).
      expect(errors).toEqual({ rtspUrl: true });
      expect(JSON.stringify(errors)).not.toContain('somepass');
    });
  });
});
