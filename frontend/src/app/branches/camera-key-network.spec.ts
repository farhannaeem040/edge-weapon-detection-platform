import { FormArray, FormBuilder, FormGroup, Validators } from '@angular/forms';

import {
  CAMERA_KEY_MAX_LENGTH,
  CAMERA_KEY_PATTERN,
  DEFAULT_RTSP_OUTPUT_PORT,
  RESERVED_CAMERA_KEYS,
} from './branch.models';
import { cameraKey, jetsonHost, notBlank, rtspOutputPort, uniqueCameraKeys } from './branch.validators';

/**
 * FS-12 / IP-14 T-285/T-289 — the client-side mirrors of the frozen CameraKey and Jetson-network
 * contracts.
 *
 * These validators exist for feedback, not enforcement: the Backend re-judges everything. That makes
 * one property more important than any individual case — a client rule must never be *stricter* than
 * the Backend's, because that becomes a mistake the Admin cannot work around. The accept-cases below
 * are therefore as load-bearing as the reject-cases.
 */
describe('FS-12 CameraKey and Jetson network validators', () => {
  const control = (value: unknown) => ({ value }) as never;

  describe('cameraKey', () => {
    it('accepts the canonical keys used by the POC deployment', () => {
      expect(cameraKey(control('front-camera'))).toBeNull();
      expect(cameraKey(control('rear-entrance'))).toBeNull();
      expect(cameraKey(control('abc'))).toBeNull();
      expect(cameraKey(control('cam1'))).toBeNull();
    });

    it('defers to notBlank on an empty value rather than reporting a malformed key', () => {
      // Stacking "malformed" on an empty box would tell the Admin their empty field is invalid.
      expect(cameraKey(control(''))).toBeNull();
      expect(cameraKey(control('   '))).toBeNull();
      expect(notBlank(control(''))).toEqual({ required: true });
    });

    it('rejects uppercase rather than silently lowercasing it', () => {
      // FS-12 §3: the administrator typed something specific; rewriting it would show them a key
      // they never chose. The Backend rejects it, so the client must too.
      expect(cameraKey(control('Front-Camera'))).toEqual({ cameraKeyPattern: true });
    });

    it('rejects spaces, underscores and slashes', () => {
      expect(cameraKey(control('front camera'))).toEqual({ cameraKeyPattern: true });
      expect(cameraKey(control('front_camera'))).toEqual({ cameraKeyPattern: true });
      expect(cameraKey(control('front/camera'))).toEqual({ cameraKeyPattern: true });
      expect(cameraKey(control('front\\camera'))).toEqual({ cameraKeyPattern: true });
    });

    it('rejects leading and trailing hyphens', () => {
      expect(cameraKey(control('-front'))).toEqual({ cameraKeyPattern: true });
      expect(cameraKey(control('front-'))).toEqual({ cameraKeyPattern: true });
    });

    it('rejects query strings, fragments and traversal', () => {
      expect(cameraKey(control('front?token=x'))).toEqual({ cameraKeyPattern: true });
      expect(cameraKey(control('front#frag'))).toEqual({ cameraKeyPattern: true });
      expect(cameraKey(control('../etc'))).toEqual({ cameraKeyPattern: true });
    });

    it('rejects every reserved key with its own distinct code', () => {
      for (const reserved of RESERVED_CAMERA_KEYS) {
        expect(cameraKey(control(reserved))).toEqual({ cameraKeyReserved: true });
      }
    });

    it('enforces the Backend length bounds', () => {
      expect(cameraKey(control('ab'))).toEqual({ cameraKeyLength: true });
      expect(cameraKey(control('a'.repeat(CAMERA_KEY_MAX_LENGTH + 1)))).toEqual({
        cameraKeyLength: true,
      });
      expect(cameraKey(control('a'.repeat(CAMERA_KEY_MAX_LENGTH)))).toBeNull();
    });

    it('mirrors the Backend regex exactly', () => {
      // If this drifts, the client starts rejecting keys the Backend accepts.
      expect(CAMERA_KEY_PATTERN.source).toBe('^[a-z0-9](?:[a-z0-9-]{1,62}[a-z0-9])$');
    });
  });

  describe('jetsonHost', () => {
    it('accepts IPv4, IPv6 and DNS hostnames', () => {
      expect(jetsonHost(control('100.98.226.80'))).toBeNull();
      expect(jetsonHost(control('192.168.1.50'))).toBeNull();
      expect(jetsonHost(control('10.20.0.15'))).toBeNull();
      expect(jetsonHost(control('jetson-ljmu.local'))).toBeNull();
      expect(jetsonHost(control('jetson-branch-01.example.internal'))).toBeNull();
      expect(jetsonHost(control('2001:db8::1'))).toBeNull();
    });

    it('rejects a scheme-prefixed host', () => {
      expect(jetsonHost(control('rtsp://100.98.226.80'))).toEqual({ jetsonHost: true });
      expect(jetsonHost(control('http://100.98.226.80'))).toEqual({ jetsonHost: true });
      expect(jetsonHost(control('https://jetson.local'))).toEqual({ jetsonHost: true });
    });

    it('rejects an embedded port, because the port is its own field', () => {
      expect(jetsonHost(control('100.98.226.80:8554'))).toEqual({ jetsonHost: true });
    });

    it('rejects paths, credentials, queries and fragments', () => {
      expect(jetsonHost(control('host/cameras'))).toEqual({ jetsonHost: true });
      expect(jetsonHost(control('user:password@host'))).toEqual({ jetsonHost: true });
      expect(jetsonHost(control('host?token=x'))).toEqual({ jetsonHost: true });
      expect(jetsonHost(control('host#frag'))).toEqual({ jetsonHost: true });
    });

    it('rejects a pre-bracketed IPv6 literal, matching the Backend', () => {
      // Bracketing is the Backend's job when it composes a URL; accepting both spellings would make
      // two different strings mean one host.
      expect(jetsonHost(control('[2001:db8::1]'))).toEqual({ jetsonHost: true });
    });

    it('defers to notBlank on an empty value', () => {
      expect(jetsonHost(control(''))).toBeNull();
    });
  });

  describe('rtspOutputPort', () => {
    it('accepts the documented default and the range boundaries', () => {
      expect(rtspOutputPort(control(DEFAULT_RTSP_OUTPUT_PORT))).toBeNull();
      expect(rtspOutputPort(control(1))).toBeNull();
      expect(rtspOutputPort(control(65535))).toBeNull();
    });

    it('rejects zero, negatives and out-of-range values', () => {
      expect(rtspOutputPort(control(0))).toEqual({ rtspOutputPort: true });
      expect(rtspOutputPort(control(-1))).toEqual({ rtspOutputPort: true });
      expect(rtspOutputPort(control(65536))).toEqual({ rtspOutputPort: true });
    });

    it('rejects decimals', () => {
      expect(rtspOutputPort(control(8554.5))).toEqual({ rtspOutputPort: true });
    });

    it('judges a string the same as a number, because a number input yields strings', () => {
      expect(rtspOutputPort(control('8554'))).toBeNull();
      expect(rtspOutputPort(control('0'))).toEqual({ rtspOutputPort: true });
      expect(rtspOutputPort(control('abc'))).toEqual({ rtspOutputPort: true });
    });

    it('reports nothing when blank, leaving the Backend to apply its own default', () => {
      expect(rtspOutputPort(control(''))).toBeNull();
      expect(rtspOutputPort(control(null))).toBeNull();
    });
  });

  describe('uniqueCameraKeys', () => {
    const formBuilder = new FormBuilder();

    function arrayOf(...keys: string[]): FormArray {
      return formBuilder.array(
        keys.map((key) =>
          formBuilder.group({ cameraKey: [key, [notBlank, cameraKey]], name: ['x', Validators.required] }),
        ),
        { validators: uniqueCameraKeys },
      ) as FormArray;
    }

    it('accepts distinct keys', () => {
      expect(arrayOf('front-camera', 'rear-entrance').errors).toBeNull();
    });

    it('rejects a duplicate key within one branch form', () => {
      expect(arrayOf('front-camera', 'front-camera').errors).toEqual({ cameraKeyDuplicate: true });
    });

    it('marks every offending row, not just the second one', () => {
      const array = arrayOf('front-camera', 'front-camera');

      for (const row of array.controls) {
        expect(row.get('cameraKey')?.errors?.['cameraKeyDuplicate']).toBeTrue();
      }
    });

    it('clears the duplicate flag once a key is changed', () => {
      const array = arrayOf('front-camera', 'front-camera');

      array.at(1).get('cameraKey')?.setValue('rear-entrance');
      array.updateValueAndValidity();

      expect(array.errors).toBeNull();
      expect(array.at(0).get('cameraKey')?.errors?.['cameraKeyDuplicate']).toBeUndefined();
    });

    it('scopes uniqueness to this form only, never globally', () => {
      // FS-12 §3: the same key in a *different* Branch is legitimate. Two independent form arrays
      // both using 'front-camera' must each be valid.
      expect(arrayOf('front-camera').errors).toBeNull();
      expect(arrayOf('front-camera').errors).toBeNull();
    });

    it('ignores blank rows rather than treating them as duplicates of each other', () => {
      const array = arrayOf('', '');

      expect(array.errors).toBeNull();
    });

    it('preserves other errors on a control while flagging a duplicate', () => {
      const array = arrayOf('Front-Camera', 'Front-Camera');
      const first = array.at(0).get('cameraKey') as FormGroup['controls'][string];

      // The pattern failure must survive alongside the duplicate flag — clearing it would hide the
      // more fundamental problem.
      expect(first.errors?.['cameraKeyPattern']).toBeTrue();
      expect(first.errors?.['cameraKeyDuplicate']).toBeTrue();
    });
  });
});
