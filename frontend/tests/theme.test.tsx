import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import React from 'react';
import { ThemeProvider, useTheme } from '../src/context/ThemeContext';
import { ThemeToggle } from '../src/components/common/ThemeToggle';

const ThemeConsumer = () => {
  const { theme, resolvedTheme, toggleTheme } = useTheme();
  return (
    <div>
      <span data-testid="theme-value">{theme}</span>
      <span data-testid="resolved-value">{resolvedTheme}</span>
      <button onClick={toggleTheme} data-testid="toggle-btn">
        Toggle
      </button>
    </div>
  );
};

describe('R1: Theme Subsystem & Persistent Toggle', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove('dark');
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('defaults to system theme and toggles dark class accordingly', () => {
    render(
      <ThemeProvider>
        <ThemeConsumer />
        <ThemeToggle />
      </ThemeProvider>
    );

    expect(screen.getByTestId('theme-value')).toHaveTextContent('system');
    expect(screen.getByTestId('resolved-value')).toHaveTextContent('light');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });

  it('persists manual theme selection in localStorage and adds dark class', () => {
    render(
      <ThemeProvider>
        <ThemeConsumer />
        <ThemeToggle />
      </ThemeProvider>
    );

    const darkBtn = screen.getByTitle('Dark theme');
    fireEvent.click(darkBtn);

    expect(screen.getByTestId('theme-value')).toHaveTextContent('dark');
    expect(screen.getByTestId('resolved-value')).toHaveTextContent('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(localStorage.getItem('recipeboxer_theme')).toBe('dark');
  });

  it('re-hydrates stored dark theme from localStorage on initial render', () => {
    localStorage.setItem('recipeboxer_theme', 'dark');

    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>
    );

    expect(screen.getByTestId('theme-value')).toHaveTextContent('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });

  it('toggleTheme switches between light and dark cleanly', () => {
    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>
    );

    const toggleBtn = screen.getByTestId('toggle-btn');
    fireEvent.click(toggleBtn); // was light -> now dark

    expect(screen.getByTestId('resolved-value')).toHaveTextContent('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);

    fireEvent.click(toggleBtn); // was dark -> now light
    expect(screen.getByTestId('resolved-value')).toHaveTextContent('light');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });
});
