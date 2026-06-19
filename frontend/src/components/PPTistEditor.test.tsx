import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import PPTistEditor from './PPTistEditor';

describe('PPTistEditor', () => {
  afterEach(() => {
    cleanup();
    document.body.style.overflow = '';
  });

  it('renders through a body portal and restores page scrolling on unmount', () => {
    const host = document.createElement('div');
    document.body.appendChild(host);

    const { container, unmount } = render(
      <PPTistEditor
        slidesJson='{"slides":[{"id":"slide-1"}]}'
        title="Java SpringPPT课件"
        onClose={() => undefined}
      />,
      { container: host },
    );

    const iframe = screen.getByTitle('PPTist Editor');
    const editorRoot = iframe.closest('.fixed');

    expect(editorRoot).not.toBeNull();
    expect(document.body.contains(editorRoot as HTMLElement)).toBe(true);
    expect(container.contains(editorRoot as HTMLElement)).toBe(false);
    expect(document.body.style.overflow).toBe('hidden');

    unmount();
    host.remove();

    expect(document.body.style.overflow).toBe('');
  });
});
