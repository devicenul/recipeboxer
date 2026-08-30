import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import {
  detectClientPlatform,
  getOsPathPlaceholder,
  ClientPlatform,
} from '../src/utils/platform';
import { SourceSelector } from '../src/components/ingestion/SourceSelector';

vi.mock('../src/utils/api', () => ({
  api: {
    startPipeline: vi.fn(),
    uploadCards: vi.fn(),
    listSourceProviders: vi.fn().mockResolvedValue([]),
    listSourceContainers: vi.fn().mockResolvedValue({ containers: [], next_page_token: null }),
    listSourceItems: vi.fn().mockResolvedValue({ items: [], next_page_token: null }),
    connectSourceProvider: vi.fn(),
    browseDirectory: vi.fn(),
    getSourceThumbnailUrl: vi.fn((providerId, itemId) => `/api/sources/${providerId}/thumbnail/${itemId}`),
  },
}));

describe('Empirical Challenger: OS-Adaptive Path & Ingestion Platform Stress Suite', () => {
  const originalNavigator = global.navigator;

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    // Restore global navigator
    Object.defineProperty(global, 'navigator', {
      value: originalNavigator,
      configurable: true,
      writable: true,
    });
  });

  const setMockNavigator = (navMock: Partial<Navigator> & { userAgentData?: any }) => {
    Object.defineProperty(global, 'navigator', {
      value: navMock,
      configurable: true,
      writable: true,
    });
  };

  // ==========================================================================
  // 1. SPECIFICATION COMPLIANCE: EXACT PLACEHOLDER STRINGS
  // ==========================================================================
  describe('1. Exact Placeholder Strings Compliance (ORIGINAL_REQUEST §R1)', () => {
    it('returns exact Windows path placeholder: C:\\Users\\username\\Documents\\RecipeScans', () => {
      const placeholder = getOsPathPlaceholder('windows');
      expect(placeholder).toBe('C:\\Users\\username\\Documents\\RecipeScans');
      expect(placeholder).toContain('\\');
      expect(placeholder.startsWith('C:\\')).toBe(true);
    });

    it('returns exact macOS path placeholder: /Users/username/Documents/RecipeScans', () => {
      const placeholder = getOsPathPlaceholder('macos');
      expect(placeholder).toBe('/Users/username/Documents/RecipeScans');
      expect(placeholder.startsWith('/Users/')).toBe(true);
      expect(placeholder).not.toContain('\\');
    });

    it('returns exact Linux / default path placeholder: /home/username/recipes', () => {
      const placeholder = getOsPathPlaceholder('linux');
      expect(placeholder).toBe('/home/username/recipes');
      expect(placeholder.startsWith('/home/')).toBe(true);
      expect(placeholder).not.toContain('\\');
    });

    it('falls back to Linux placeholder for undefined or unrecognized platform argument', () => {
      expect(getOsPathPlaceholder('unknown' as any)).toBe('/home/username/recipes');
    });
  });

  // ==========================================================================
  // 2. CLIENT HINTS DETECTION (userAgentData.platform)
  // ==========================================================================
  describe('2. Modern Chromium Client Hints (navigator.userAgentData.platform)', () => {
    it('detects Windows from userAgentData variations', () => {
      const winVariants = ['Windows', 'windows', 'Windows NT', 'Win32', 'WIN64'];
      winVariants.forEach((platform) => {
        const mockNav = {
          userAgentData: { platform },
        } as unknown as Navigator;
        expect(detectClientPlatform(mockNav)).toBe('windows');
      });
    });

    it('detects macOS from userAgentData variations', () => {
      const macVariants = ['macOS', 'macOS Sonoma', 'Mac OS X', 'mac', 'MACOS'];
      macVariants.forEach((platform) => {
        const mockNav = {
          userAgentData: { platform },
        } as unknown as Navigator;
        expect(detectClientPlatform(mockNav)).toBe('macos');
      });
    });

    it('detects Linux from userAgentData variations', () => {
      const linuxVariants = ['Linux', 'linux', 'X11', 'x11', 'GNU/Linux'];
      linuxVariants.forEach((platform) => {
        const mockNav = {
          userAgentData: { platform },
        } as unknown as Navigator;
        expect(detectClientPlatform(mockNav)).toBe('linux');
      });
    });

    it('falls through to standard platform/userAgent when userAgentData.platform is empty or unrecognized', () => {
      const mockNav = {
        userAgentData: { platform: '' },
        platform: 'Win32',
        userAgent: 'Mozilla/5.0 (Windows NT 10.0)',
      } as unknown as Navigator;
      expect(detectClientPlatform(mockNav)).toBe('windows');
    });
  });

  // ==========================================================================
  // 3. LEGACY NAVIGATOR & USER-AGENT MATRIX STRESS-TESTING
  // ==========================================================================
  describe('3. Legacy Navigator & Real-World User-Agent Matrix', () => {
    it('correctly classifies diverse Windows browser environments', () => {
      const windowsFixtures = [
        { platform: 'Win32', ua: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36' },
        { platform: 'Win64; x64', ua: 'Mozilla/5.0 (Windows NT 10.0; WOW64; Trident/7.0; rv:11.0) like Gecko' },
        { platform: 'Win32', ua: 'Mozilla/5.0 (Windows NT 6.1; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0' },
        { platform: 'Windows', ua: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; Edg/128.0.0.0)' },
      ];

      windowsFixtures.forEach(({ platform, ua }) => {
        const nav = { platform, userAgent: ua } as unknown as Navigator;
        expect(detectClientPlatform(nav)).toBe('windows');
      });
    });

    it('correctly classifies diverse macOS browser environments', () => {
      const macFixtures = [
        { platform: 'MacIntel', ua: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36' },
        { platform: 'MacIntel', ua: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15' },
        { platform: 'MacIntel', ua: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:129.0) Gecko/20100101 Firefox/129.0' },
        { platform: 'MacPPC', ua: 'Mozilla/5.0 (Macintosh; PPC Mac OS X 10.5; rv:8.0) Gecko/20100101 Firefox/8.0' },
      ];

      macFixtures.forEach(({ platform, ua }) => {
        const nav = { platform, userAgent: ua } as unknown as Navigator;
        expect(detectClientPlatform(nav)).toBe('macos');
      });
    });

    it('correctly classifies diverse Linux & BSD desktop environments', () => {
      const linuxFixtures = [
        { platform: 'Linux x86_64', ua: 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:129.0) Gecko/20100101 Firefox/129.0' },
        { platform: 'Linux i686', ua: 'Mozilla/5.0 (X11; Linux i686) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36' },
        { platform: 'Linux aarch64', ua: 'Mozilla/5.0 (X11; Fedora; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36' },
        { platform: 'X11', ua: 'Mozilla/5.0 (X11; Linux x86_64)' },
      ];

      linuxFixtures.forEach(({ platform, ua }) => {
        const nav = { platform, userAgent: ua } as unknown as Navigator;
        expect(detectClientPlatform(nav)).toBe('linux');
      });
    });

    it('handles mobile user agents gracefully (Android, iPhone, iPad)', () => {
      // Android
      const androidNav = {
        platform: 'Linux armv8l',
        userAgent: 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36',
      } as unknown as Navigator;
      expect(detectClientPlatform(androidNav)).toBe('linux');

      // iPhone
      const iphoneNav = {
        platform: 'iPhone',
        userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_6_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1',
      } as unknown as Navigator;
      // iPhone user agents contain "like Mac OS X", resulting in macos
      expect(detectClientPlatform(iphoneNav)).toBe('macos');
    });

    it('handles edge case inputs (undefined navigator, null properties, empty object)', () => {
      expect(detectClientPlatform(undefined)).toBeDefined();
      expect(detectClientPlatform({} as unknown as Navigator)).toBe('linux');
      expect(detectClientPlatform({ platform: '', userAgent: '' } as unknown as Navigator)).toBe('linux');
      expect(detectClientPlatform({ platform: 'UnknownOS', userAgent: 'CustomBrowser/1.0' } as unknown as Navigator)).toBe('linux');
    });
  });

  // ==========================================================================
  // 4. COMPONENT INTEGRATION IN SourceSelector.tsx
  // ==========================================================================
  describe('4. SourceSelector Ingestion Path Placeholder Rendering', () => {
    const defaultProps = {
      sourceType: 'local' as const,
      onSourceTypeChange: vi.fn(),
      localMethod: 'folder' as const,
      onLocalMethodChange: vi.fn(),
      folderPath: '',
      onFolderPathChange: vi.fn(),
      uploadedFiles: [],
      onUploadedFilesChange: vi.fn(),
      selectedContainer: null,
      onSelectContainer: vi.fn(),
      categoryFilter: 'FOOD',
      onCategoryFilterChange: vi.fn(),
      bucketName: '',
      onBucketNameChange: vi.fn(),
      prefix: '',
      onPrefixChange: vi.fn(),
    };

    it('renders Windows placeholder when client environment is Windows', async () => {
      setMockNavigator({
        platform: 'Win32',
        userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
      });

      render(<SourceSelector {...defaultProps} />);

      const input = await screen.findByLabelText(/Local Scans Directory Path/i);
      expect(input).toHaveAttribute('placeholder', 'C:\\Users\\username\\Documents\\RecipeScans');
    });

    it('renders macOS placeholder when client environment is macOS', async () => {
      setMockNavigator({
        platform: 'MacIntel',
        userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
      });

      render(<SourceSelector {...defaultProps} />);

      const input = await screen.findByLabelText(/Local Scans Directory Path/i);
      expect(input).toHaveAttribute('placeholder', '/Users/username/Documents/RecipeScans');
    });

    it('renders Linux placeholder when client environment is Linux', async () => {
      setMockNavigator({
        platform: 'Linux x86_64',
        userAgent: 'Mozilla/5.0 (X11; Linux x86_64)',
      });

      render(<SourceSelector {...defaultProps} />);

      const input = await screen.findByLabelText(/Local Scans Directory Path/i);
      expect(input).toHaveAttribute('placeholder', '/home/username/recipes');
    });

    it('retains user input value while keeping placeholder configured', async () => {
      setMockNavigator({
        platform: 'Win32',
        userAgent: 'Mozilla/5.0 (Windows NT 10.0)',
      });

      const onFolderPathChange = vi.fn();
      const { rerender } = render(
        <SourceSelector
          {...defaultProps}
          folderPath={'D:\\FamilyRecipes\\Box1'}
          onFolderPathChange={onFolderPathChange}
        />
      );

      const input = (await screen.findByLabelText(/Local Scans Directory Path/i)) as HTMLInputElement;
      expect(input.value).toBe('D:\\FamilyRecipes\\Box1');
      expect(input.getAttribute('placeholder')).toBe('C:\\Users\\username\\Documents\\RecipeScans');

      // Clear input value and verify placeholder is still present
      rerender(
        <SourceSelector
          {...defaultProps}
          folderPath=""
          onFolderPathChange={onFolderPathChange}
        />
      );
      expect(input.value).toBe('');
      expect(input.getAttribute('placeholder')).toBe('C:\\Users\\username\\Documents\\RecipeScans');
    });

    it('preserves placeholder after switching between Local Method tabs (folder -> files -> folder)', async () => {
      setMockNavigator({
        platform: 'MacIntel',
        userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
      });

      const onLocalMethodChange = vi.fn();
      const { rerender } = render(
        <SourceSelector
          {...defaultProps}
          localMethod="folder"
          onLocalMethodChange={onLocalMethodChange}
        />
      );

      expect(await screen.findByPlaceholderText('/Users/username/Documents/RecipeScans')).toBeInTheDocument();

      // Switch to direct file upload
      rerender(
        <SourceSelector
          {...defaultProps}
          localMethod="files"
          onLocalMethodChange={onLocalMethodChange}
        />
      );
      expect(screen.queryByPlaceholderText('/Users/username/Documents/RecipeScans')).not.toBeInTheDocument();

      // Switch back to folder path
      rerender(
        <SourceSelector
          {...defaultProps}
          localMethod="folder"
          onLocalMethodChange={onLocalMethodChange}
        />
      );
      expect(await screen.findByPlaceholderText('/Users/username/Documents/RecipeScans')).toBeInTheDocument();
    });
  });
});
