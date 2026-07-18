import { Component, signal } from '@angular/core';
import { RouterOutlet } from '@angular/router';

/**
 * The application root. It is deliberately thin: it hosts the router-outlet and nothing else, so
 * the public login view renders standalone while the authenticated views render inside the shared
 * shell (see `ShellComponent`, wired in `app.routes.ts`).
 */
@Component({
  selector: 'app-root',
  imports: [RouterOutlet],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  /** The product name (rebranded from the foundation scaffold). Used by the shell and login views. */
  protected readonly title = signal('LJMU AI Security Platform');
}
