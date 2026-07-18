import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { App } from './app';

describe('App', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      // The root hosts a router-outlet, which needs a router context to activate.
      providers: [provideRouter([])],
    }).compileComponents();
  });

  it('should create the app', () => {
    const fixture = TestBed.createComponent(App);
    expect(fixture.componentInstance).toBeTruthy();
  });

  it('carries the rebranded product name', () => {
    const fixture = TestBed.createComponent(App);
    // Rebranded from the foundation scaffold to the LJMU AI Security Platform.
    expect(fixture.componentInstance['title']()).toBe('LJMU AI Security Platform');
  });

  it('provides a router-outlet for the feature routes', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('router-outlet')).not.toBeNull();
  });
});
