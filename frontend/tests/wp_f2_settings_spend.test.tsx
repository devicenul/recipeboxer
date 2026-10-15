import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import { QualityProfileSelector } from '../src/components/settings/QualityProfileSelector';
import { SpendCapsSection } from '../src/components/settings/SpendCapsSection';
import { ApiKeySection } from '../src/components/settings/ApiKeySection';
import { CostEstimateCalculator } from '../src/components/settings/CostEstimateCalculator';
import { OnboardingBanner } from '../src/components/layout/OnboardingBanner';
import { SpendMeter } from '../src/components/layout/SpendMeter';
import { StatusBar } from '../src/components/layout/StatusBar';
import { SettingsPage } from '../src/pages/SettingsPage';
import { SettingsProvider } from '../src/context/SettingsContext';
import App from '../src/App';
import { api } from '../src/utils/api';

// Mock API endpoints for settings
vi.mock('../src/utils/api', () => ({
  api: {
    getSettings: vi.fn().mockResolvedValue({
      quality_profile: 'balanced',
      stage_models: { FINGERPRINT: 'gemini-3.7-flash' },
      spend_cap_usd: 2.5,
      monthly_cap_usd: 10.0,
      rpm_limit: 60,
      extra_effort: true,
      embeddings_enabled: false,
      has_api_key: true,
      key_source: 'keyring',
    }),
    updateSettings: vi.fn().mockImplementation((payload) =>
      Promise.resolve({
        quality_profile: payload.quality_profile || 'balanced',
        stage_models: payload.stage_models || {},
        spend_cap_usd: payload.spend_cap_usd ?? null,
        monthly_cap_usd: payload.monthly_cap_usd ?? null,
        rpm_limit: payload.rpm_limit ?? 60,
        extra_effort: !!payload.extra_effort,
        embeddings_enabled: !!payload.embeddings_enabled,
        has_api_key: true,
        key_source: 'keyring',
      })
    ),
    setApiKey: vi.fn().mockResolvedValue({
      success: true,
      has_api_key: true,
      key_source: 'keyring',
      message: 'Gemini API key saved successfully via keyring',
    }),
    deleteApiKey: vi.fn().mockResolvedValue({
      success: true,
      has_api_key: false,
      key_source: 'none',
      message: 'Gemini API key removed successfully',
    }),
    getCostEstimate: vi.fn().mockImplementation((nCards, profile) => {
      if (profile === 'economy') {
        // Return null to test unpriced model scenario
        return Promise.resolve({
          low: null,
          expected: null,
          high: null,
          assumptions: { unpriced: true },
        });
      }
      return Promise.resolve({
        low: 0.02 * nCards,
        expected: 0.05 * nCards,
        high: 0.12 * nCards,
        assumptions: { n_cards: nCards, profile },
      });
    }),
    getPrivacyNotice: vi.fn().mockResolvedValue({
      notice:
        "When using Google's unpaid / free-tier Gemini API, prompt data and card images may be reviewed by human reviewers and utilized by Google to train and improve products.",
      terms_url: 'https://ai.google.dev/gemini-api/terms',
      privacy_policy_url: 'https://policies.google.com/privacy',
      paid_vs_free_summary:
        'Free tier: Data may be retained and used for Google product improvement. Paid tier: Data is not used to train Google models and remains strictly private.',
    }),
    getStatus: vi.fn().mockResolvedValue({
      session_id: 'sess_f2_test',
      status: 'IDLE',
      mode: 'parallel',
      progress: {
        total_scans: 10,
        fingerprinted: 10,
        proposals_generated: 4,
        approved_recipes: 0,
        approved_pairs: 0,
        recipes_extracted: 0,
        current_stage: 'Idle',
        percent_complete: 100,
      },
      token_usage: { prompt_tokens: 1200, candidates_tokens: 450, thinking_tokens: 200, total_tokens: 1850 },
      elapsed_seconds: 42,
      spend_usd: 0.35,
    }),
    listSessions: vi.fn().mockResolvedValue([]),
    listRecipes: vi.fn().mockResolvedValue([]),
    getHitlPending: vi.fn().mockResolvedValue({
      session_id: 'sess_f2_test',
      status: 'HITL_PENDING',
      proposed_pairs: [],
      unassigned_cards: [],
      total_proposals: 0,
      total_unassigned: 0,
    }),
    getCardImageUrl: vi.fn((id) => `/api/cards/${id}/image`),
  },
}));

let sseCallbacks: any = {};

vi.mock('../src/hooks/useSSE', () => ({
  useSSE: (opts: any) => {
    sseCallbacks = opts;
    return {
      isConnected: true,
      isReconnecting: false,
      reconnectCount: 0,
      lastEvent: null,
      reconnect: vi.fn(),
    };
  },
}));

describe('WP-F2: Settings UI, Usage Limits, Cost Estimator & Spend Meter', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sseCallbacks = {};
  });

  // ---------------------------------------------------------------------------
  // 1. Quality Profile Selector Tests
  // ---------------------------------------------------------------------------
  describe('QualityProfileSelector Component', () => {
    it('renders all three profiles with plain-language trade-offs and specs', () => {
      const onChange = vi.fn();
      render(<QualityProfileSelector value="balanced" onChange={onChange} />);

      expect(screen.getByText('Economy')).toBeInTheDocument();
      expect(screen.getByText('Lowest cost & fastest')).toBeInTheDocument();

      expect(screen.getByText('Balanced')).toBeInTheDocument();
      expect(screen.getByText('Recommended default')).toBeInTheDocument();

      expect(screen.getByText('Best')).toBeInTheDocument();
      expect(screen.getByText('Maximum accuracy for difficult cards')).toBeInTheDocument();
    });

    it('displays notice that Best profile requires a paid Gemini API key', () => {
      const onChange = vi.fn();
      render(<QualityProfileSelector value="best" onChange={onChange} />);

      // Warning inside the card
      expect(
        screen.getByText(/Paid key required — Pro models are not available on Google's free tier/i)
      ).toBeInTheDocument();

      // Expanded warning banner when selected
      expect(screen.getByTestId('best-profile-warning')).toBeInTheDocument();
      expect(
        screen.getByText(/Pro models are unavailable on Google's free-tier API quotas/i)
      ).toBeInTheDocument();
    });

    it('fires onChange callback when clicking another profile', () => {
      const onChange = vi.fn();
      render(<QualityProfileSelector value="balanced" onChange={onChange} />);

      const economyCard = screen.getByTestId('profile-card-economy');
      fireEvent.click(economyCard);
      expect(onChange).toHaveBeenCalledWith('economy');

      const bestCard = screen.getByTestId('profile-card-best');
      fireEvent.click(bestCard);
      expect(onChange).toHaveBeenCalledWith('best');
    });
  });

  // ---------------------------------------------------------------------------
  // 2. Spend Caps & Feature Toggles Tests
  // ---------------------------------------------------------------------------
  describe('SpendCapsSection Component', () => {
    it('renders per-run cap, monthly cap, and RPM limit fields', () => {
      const onChange = vi.fn();
      render(
        <SpendCapsSection
          spendCapUsd={2.5}
          monthlyCapUsd={15.0}
          rpmLimit={60}
          extraEffort={true}
          embeddingsEnabled={false}
          stageModels={{ FINGERPRINT: 'gemini-3.7-flash' }}
          onChange={onChange}
        />
      );

      const runCapInput = screen.getByLabelText(/Per-Run Spend Cap/i) as HTMLInputElement;
      expect(runCapInput.value).toBe('2.5');

      const monthlyCapInput = screen.getByLabelText(/Monthly Spend Cap/i) as HTMLInputElement;
      expect(monthlyCapInput.value).toBe('15');

      const rpmInput = screen.getByLabelText(/Requests Per Minute/i) as HTMLInputElement;
      expect(rpmInput.value).toBe('60');
    });

    it('triggers onChange when updating spend caps and rate limits', () => {
      const onChange = vi.fn();
      const ControlledWrapper = () => {
        const [cap, setCap] = React.useState<number | null>(null);
        const [rpm, setRpm] = React.useState<number>(60);
        return (
          <SpendCapsSection
            spendCapUsd={cap}
            monthlyCapUsd={null}
            rpmLimit={rpm}
            extraEffort={false}
            embeddingsEnabled={false}
            stageModels={{}}
            onChange={(u) => {
              onChange(u);
              if (u.spend_cap_usd !== undefined) setCap(u.spend_cap_usd);
              if (u.rpm_limit !== undefined) setRpm(u.rpm_limit);
            }}
          />
        );
      };
      render(<ControlledWrapper />);

      const runCapInput = screen.getByLabelText(/Per-Run Spend Cap/i);
      fireEvent.change(runCapInput, { target: { value: '3.75' } });
      expect(onChange).toHaveBeenCalledWith({ spend_cap_usd: 3.75 });

      fireEvent.change(runCapInput, { target: { value: '' } });
      expect(onChange).toHaveBeenCalledWith({ spend_cap_usd: null });

      const rpmInput = screen.getByLabelText(/Requests Per Minute/i);
      fireEvent.change(rpmInput, { target: { value: '30' } });
      expect(onChange).toHaveBeenCalledWith({ rpm_limit: 30 });
    });

    it('toggles extra effort and embeddings flags', () => {
      const onChange = vi.fn();
      render(
        <SpendCapsSection
          spendCapUsd={null}
          monthlyCapUsd={null}
          rpmLimit={60}
          extraEffort={false}
          embeddingsEnabled={false}
          stageModels={{}}
          onChange={onChange}
        />
      );

      const extraEffortCheckbox = screen.getByLabelText(/Extra effort for hard-to-read cards/i);
      fireEvent.click(extraEffortCheckbox);
      expect(onChange).toHaveBeenCalledWith({ extra_effort: true });

      const embeddingsCheckbox = screen.getByLabelText(/Embeddings-based semantic pairing/i);
      fireEvent.click(embeddingsCheckbox);
      expect(onChange).toHaveBeenCalledWith({ embeddings_enabled: true });
    });

    it('expands collapsed advanced model overrides and updates stage models', () => {
      const onChange = vi.fn();
      render(
        <SpendCapsSection
          spendCapUsd={null}
          monthlyCapUsd={null}
          rpmLimit={60}
          extraEffort={false}
          embeddingsEnabled={false}
          stageModels={{ FINGERPRINT: 'gemini-3.7-flash' }}
          onChange={onChange}
        />
      );

      // Initially collapsed
      expect(screen.queryByLabelText(/Card Fingerprinting \(Stage 2\)/i)).not.toBeInTheDocument();

      // Click to toggle
      const toggleButton = screen.getByTestId('toggle-advanced-overrides');
      fireEvent.click(toggleButton);

      // Now visible
      const fpInput = screen.getByLabelText(/Card Fingerprinting \(Stage 2\)/i) as HTMLInputElement;
      expect(fpInput).toBeInTheDocument();
      expect(fpInput.value).toBe('gemini-3.7-flash');

      fireEvent.change(fpInput, { target: { value: 'gemini-3.6-flash' } });
      expect(onChange).toHaveBeenCalledWith({
        stage_models: { FINGERPRINT: 'gemini-3.6-flash' },
      });
    });
  });

  // ---------------------------------------------------------------------------
  // 3. API Key & Security Section Tests
  // ---------------------------------------------------------------------------
  describe('ApiKeySection Component', () => {
    it('shows key status and storage source only, never displaying secret key', () => {
      render(
        <ApiKeySection
          hasApiKey={true}
          keySource="keyring"
          onSaveKey={vi.fn()}
          onRemoveKey={vi.fn()}
        />
      );

      expect(screen.getByText('API Key Configured')).toBeInTheDocument();
      expect(screen.getByText('Ready')).toBeInTheDocument();
      expect(screen.getByText('keyring')).toBeInTheDocument();

      // Verify the input is password type
      const input = screen.getByTestId('api-key-input') as HTMLInputElement;
      expect(input.type).toBe('password');
      expect(input.value).toBe('');
    });

    it('submits API key and calls onSaveKey', async () => {
      const onSaveKey = vi.fn().mockResolvedValue(undefined);
      render(
        <ApiKeySection
          hasApiKey={false}
          keySource="none"
          onSaveKey={onSaveKey}
          onRemoveKey={vi.fn()}
        />
      );

      const input = screen.getByTestId('api-key-input');
      fireEvent.change(input, { target: { value: 'AIzaSyTestKey12345' } });

      const saveBtn = screen.getByTestId('save-api-key-button');
      fireEvent.click(saveBtn);

      await waitFor(() => {
        expect(onSaveKey).toHaveBeenCalledWith('AIzaSyTestKey12345');
      });
    });

    it('calls onRemoveKey when clicking Remove Key button', async () => {
      const onRemoveKey = vi.fn().mockResolvedValue(undefined);
      vi.spyOn(window, 'confirm').mockReturnValue(true);

      render(
        <ApiKeySection
          hasApiKey={true}
          keySource="settings_file"
          onSaveKey={vi.fn()}
          onRemoveKey={onRemoveKey}
        />
      );

      const removeBtn = screen.getByTestId('remove-api-key-button');
      fireEvent.click(removeBtn);

      await waitFor(() => {
        expect(onRemoveKey).toHaveBeenCalled();
      });
    });

    it('displays free-tier vs paid explainer and privacy terms links', async () => {
      render(
        <ApiKeySection
          hasApiKey={true}
          keySource="keyring"
          onSaveKey={vi.fn()}
          onRemoveKey={vi.fn()}
        />
      );

      // Free tier low limits phrase
      expect(
        screen.getByText(/very low daily limits, may change; see Google's current limits/i)
      ).toBeInTheDocument();

      // Pro models unavailable
      expect(screen.getByText(/Pro models \(such as gemini-3.1-pro\) are unavailable on the free tier/i)).toBeInTheDocument();

      // Privacy recommendation
      expect(screen.getByText(/paid Google Cloud API key/i)).toBeInTheDocument();

      // Privacy notice from endpoint
      await waitFor(() => {
        expect(screen.getByText(/Google Gemini API Terms/i)).toBeInTheDocument();
        expect(screen.getByText(/Google Privacy Policy/i)).toBeInTheDocument();
      });
    });
  });

  // ---------------------------------------------------------------------------
  // 4. Cost Estimate Calculator Tests
  // ---------------------------------------------------------------------------
  describe('CostEstimateCalculator Component', () => {
    it('calculates and renders formatted cost estimate for cards count', async () => {
      render(<CostEstimateCalculator currentProfile="balanced" cardCountDefault={10} />);

      await waitFor(() => {
        expect(api.getCostEstimate).toHaveBeenCalledWith(10, 'balanced');
      });

      // 10 cards * 0.02 = $0.2000, 10 * 0.05 = $0.5000, 10 * 0.12 = $1.2000
      expect(screen.getByTestId('estimate-low')).toHaveTextContent('$0.2000');
      expect(screen.getByTestId('estimate-expected')).toHaveTextContent('$0.5000');
      expect(screen.getByTestId('estimate-high')).toHaveTextContent('$1.2000');
    });

    it('renders "unknown - model not priced" when estimates are null (never $0)', async () => {
      render(<CostEstimateCalculator currentProfile="economy" cardCountDefault={10} />);

      await waitFor(() => {
        expect(api.getCostEstimate).toHaveBeenCalledWith(10, 'economy');
      });

      // Check all 3 boundaries show unknown, not $0.00
      expect(screen.getByTestId('estimate-low')).toHaveTextContent('unknown - model not priced');
      expect(screen.getByTestId('estimate-expected')).toHaveTextContent('unknown - model not priced');
      expect(screen.getByTestId('estimate-high')).toHaveTextContent('unknown - model not priced');
    });
  });

  // ---------------------------------------------------------------------------
  // 5. Onboarding Banner Tests
  // ---------------------------------------------------------------------------
  describe('OnboardingBanner Component', () => {
    it('renders prominent banner when analysis_blocked_reason is NO_API_KEY', () => {
      const onNavigate = vi.fn();
      render(
        <OnboardingBanner
          analysisBlockedReason="NO_API_KEY"
          onboardingHint="Configure key at /api/settings/api-key"
          onNavigateToSettings={onNavigate}
        />
      );

      expect(screen.getByTestId('onboarding-no-api-key-banner')).toBeInTheDocument();
      expect(
        screen.getByText(/Card scanning, thumbnail extraction, and physical feature screening work completely offline/i)
      ).toBeInTheDocument();

      const btn = screen.getByTestId('onboarding-settings-link');
      fireEvent.click(btn);
      expect(onNavigate).toHaveBeenCalled();
    });

    it('renders nothing when analysis_blocked_reason is null or different', () => {
      const { container } = render(
        <OnboardingBanner
          analysisBlockedReason={null}
          onNavigateToSettings={vi.fn()}
        />
      );
      expect(container.firstChild).toBeNull();
    });
  });

  // ---------------------------------------------------------------------------
  // 6. Spend Meter & Status Bar Tests
  // ---------------------------------------------------------------------------
  describe('SpendMeter & StatusBar Telemetry', () => {
    it('renders live spend and cap progress percentage', () => {
      render(
        <SpendMeter
          spendUsd={0.5}
          spendCapUsd={2.0}
          totalCalls={8}
          isBudgetExceeded={false}
        />
      );

      expect(screen.getByText('$0.5000')).toBeInTheDocument();
      expect(screen.getByText('$2.00')).toBeInTheDocument();
      expect(screen.getByText('25%')).toBeInTheDocument();
      expect(screen.getByText('8 calls')).toBeInTheDocument();
    });

    it('displays prominent Budget Exceeded pause badge when budget is exceeded', () => {
      const onAdjust = vi.fn();
      render(
        <SpendMeter
          spendUsd={2.55}
          spendCapUsd={2.5}
          totalCalls={15}
          isBudgetExceeded={true}
          onNavigateToSettings={onAdjust}
        />
      );

      expect(screen.getByTestId('budget-exceeded-badge')).toBeInTheDocument();
      expect(screen.getByText('Budget Exceeded — Paused')).toBeInTheDocument();

      const adjustBtn = screen.getByText('Adjust Cap');
      fireEvent.click(adjustBtn);
      expect(onAdjust).toHaveBeenCalled();
    });

    it('renders "unknown" when spendUsd is null or undefined without throwing', () => {
      const { unmount } = render(
        <SpendMeter
          spendUsd={null}
          spendCapUsd={2.5}
        />
      );
      expect(screen.getByText('unknown')).toBeInTheDocument();
      expect(screen.queryByText('$0.0000')).not.toBeInTheDocument();
      unmount();

      render(
        <SpendMeter
          spendUsd={undefined}
        />
      );
      expect(screen.getByText('unknown')).toBeInTheDocument();
      expect(screen.queryByText('$0.0000')).not.toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------------------
  // 7. Full Settings Page Integration Tests
  // ---------------------------------------------------------------------------
  describe('SettingsPage Full View Integration', () => {
    it('renders all settings sections and saves user updates', async () => {
      render(
        <SettingsProvider>
          <SettingsPage />
        </SettingsProvider>
      );

      // Wait for settings to load
      await waitFor(() => {
        expect(screen.getByText('Settings & Usage Controls')).toBeInTheDocument();
      });

      expect(screen.getByText('Quality & Cost Profile')).toBeInTheDocument();
      expect(screen.getByText('Spend Caps & Rate Limits')).toBeInTheDocument();
      expect(screen.getByText('Pre-Run Pipeline Cost Estimator')).toBeInTheDocument();
      expect(screen.getByText('Google Gemini API Key')).toBeInTheDocument();

      // Click save button
      const saveAllBtn = screen.getByTestId('save-all-settings-button');
      fireEvent.click(saveAllBtn);

      await waitFor(() => {
        expect(api.updateSettings).toHaveBeenCalled();
        expect(screen.getByText(/All settings and spend caps saved successfully/i)).toBeInTheDocument();
      });
    });
  });

  // ---------------------------------------------------------------------------
  // 8. App Navigation & SSE Spend Event Integration
  // ---------------------------------------------------------------------------
  describe('App Navigation & SSE Live Spend Integration', () => {
    it('allows navigating to Settings tab from header navigation', async () => {
      render(<App />);

      await waitFor(() => {
        expect(screen.getByText('RecipeBoxer')).toBeInTheDocument();
      });

      const settingsNavBtn = screen.getAllByText('5. Settings')[0];
      fireEvent.click(settingsNavBtn);

      await waitFor(() => {
        expect(screen.getByText('Settings & Usage Controls')).toBeInTheDocument();
      });
    });

    it('updates live spend in StatusBar when SSE emits a spend event', async () => {
      render(<App />);

      await waitFor(() => {
        expect(screen.getByTestId('spend-meter')).toBeInTheDocument();
      });

      // Trigger SSE spend event callback
      expect(sseCallbacks.onSpend).toBeDefined();

      sseCallbacks.onSpend({
        session_id: 'sess_f2_test',
        spend_usd: 1.45,
        total_calls: 12,
        snapshot: { total_calls: 12, total_usd: 1.45 },
      });

      await waitFor(() => {
        expect(screen.getByText('$1.4500')).toBeInTheDocument();
        expect(screen.getByText('12 calls')).toBeInTheDocument();
      });
    });
  });
});
