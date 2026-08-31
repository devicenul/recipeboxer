import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import { SessionLauncher } from '../src/components/ingestion/SessionLauncher';
import { ProgressDashboard } from '../src/components/ingestion/ProgressDashboard';
import { api } from '../src/utils/api';
import { PipelineState, PipelineStatus, SessionSummary } from '../src/types/pipeline';

describe('Milestone 2: Incremental Ingestion with Duplicate-Skipping (Requirement R2)', () => {
  const sampleSessions: SessionSummary[] = [
    {
      session_id: 'sess_prev_001',
      status: PipelineState.COMPLETED,
      card_count: 5,
      recipe_count: 3,
      created_at: '2026-08-30T12:00:00Z',
    },
  ];

  beforeEach(() => {
    vi.spyOn(api, 'listSessions').mockResolvedValue(sampleSessions);
    vi.spyOn(api, 'startPipeline').mockResolvedValue({
      session_id: 'sess_new_999',
      status: PipelineState.SCANNING,
      total_scans_found: 10,
      mode: 'parallel',
      message: 'Started',
    });
    vi.spyOn(api, 'uploadCards').mockResolvedValue({
      session_id: 'sess_new_999',
      card_ids: ['c1', 'c2'],
      count: 2,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('SessionLauncher - Baseline Project Selection', () => {
    it('renders baseline archive options in SessionLauncher', async () => {
      render(<SessionLauncher onSessionStarted={vi.fn()} />);

      expect(screen.getByText(/Baseline Project Archive \(Incremental Ingestion\)/i)).toBeInTheDocument();
      expect(screen.getByText('None')).toBeInTheDocument();
      expect(screen.getByText('Upload Archive')).toBeInTheDocument();
      expect(screen.getByText('Past Session')).toBeInTheDocument();
      expect(screen.getByText('File Path')).toBeInTheDocument();
    });

    it('switches baseline mode to Past Session and lists historical sessions', async () => {
      render(<SessionLauncher onSessionStarted={vi.fn()} />);

      const pastSessionBtn = screen.getByRole('button', { name: /Past Session/i });
      fireEvent.click(pastSessionBtn);

      await waitFor(() => {
        expect(screen.getByText(/Select Prior Session as Baseline:/i)).toBeInTheDocument();
      });

      const select = screen.getByLabelText(/Select Prior Session as Baseline/i);
      expect(select).toBeInTheDocument();
      expect(screen.getByText(/sess_prev_001/i)).toBeInTheDocument();
    });

    it('switches baseline mode to File Path and allows typing path', async () => {
      render(<SessionLauncher onSessionStarted={vi.fn()} />);

      const filePathBtn = screen.getByRole('button', { name: /File Path/i });
      fireEvent.click(filePathBtn);

      const pathInput = screen.getByPlaceholderText(/e\.g\. \/path\/to\/archive\.recipeboxer/i) as HTMLInputElement;
      expect(pathInput).toBeInTheDocument();

      fireEvent.change(pathInput, { target: { value: 'C:\\scans\\baseline.recipeboxer' } });
      expect(pathInput.value).toBe('C:\\scans\\baseline.recipeboxer');
    });

    it('submits folder start with baseline_project_path', async () => {
      const onStarted = vi.fn();
      render(<SessionLauncher onSessionStarted={onStarted} />);

      // Enter scans directory
      const folderInput = screen.getByLabelText(/Local Scans Directory Path/i);
      fireEvent.change(folderInput, { target: { value: 'C:\\my_scans' } });

      // Select File Path baseline
      fireEvent.click(screen.getByRole('button', { name: /File Path/i }));
      const pathInput = screen.getByPlaceholderText(/e\.g\. \/path\/to\/archive\.recipeboxer/i);
      fireEvent.change(pathInput, { target: { value: 'C:\\my_scans\\baseline.recipeboxer' } });

      // Click Launch
      const launchBtn = screen.getByRole('button', { name: /Launch Digitization Pipeline/i });
      fireEvent.click(launchBtn);

      await waitFor(() => {
        expect(api.startPipeline).toHaveBeenCalledWith(
          expect.objectContaining({
            scans_dir: 'C:\\my_scans',
            baseline_project_path: 'C:\\my_scans\\baseline.recipeboxer',
          })
        );
        expect(onStarted).toHaveBeenCalledWith('sess_new_999');
      });
    });

    it('submits folder start with baseline_session_id', async () => {
      const onStarted = vi.fn();
      render(<SessionLauncher onSessionStarted={onStarted} />);

      // Enter scans directory
      const folderInput = screen.getByLabelText(/Local Scans Directory Path/i);
      fireEvent.change(folderInput, { target: { value: 'C:\\my_scans' } });

      // Select Past Session baseline
      const pastSessionBtn = screen.getByRole('button', { name: /Past Session/i });
      fireEvent.click(pastSessionBtn);

      await waitFor(() => {
        expect(screen.getByText(/Select Prior Session as Baseline:/i)).toBeInTheDocument();
        expect(screen.getByText(/sess_prev_001/i)).toBeInTheDocument();
      });

      const select = screen.getByLabelText(/Select Prior Session as Baseline/i);
      fireEvent.change(select, { target: { value: 'sess_prev_001' } });

      // Click Launch
      const launchBtn = screen.getByRole('button', { name: /Launch Digitization Pipeline/i });
      fireEvent.click(launchBtn);

      await waitFor(() => {
        expect(api.startPipeline).toHaveBeenCalledWith(
          expect.objectContaining({
            scans_dir: 'C:\\my_scans',
            baseline_session_id: 'sess_prev_001',
          })
        );
        expect(onStarted).toHaveBeenCalledWith('sess_new_999');
      });
    });
  });

  describe('ProgressDashboard - Incremental Ingestion Telemetry', () => {
    it('displays Duplicates Skipped and Baseline Inherited badges when cards are skipped', () => {
      const status: PipelineStatus = {
        session_id: 'sess_inc_123',
        status: PipelineState.COMPLETED,
        progress: {
          total_scans: 10,
          fingerprinted: 10,
          proposals_generated: 2,
          approved_recipes: 2,
          recipes_extracted: 2,
          percent_complete: 100,
          skipped_cards: 6,
          newly_processed_cards: 4,
          baseline_recipes_count: 5,
        },
        token_usage: {
          prompt_tokens: 1200,
          candidates_tokens: 600,
          thinking_tokens: 0,
          total_tokens: 1800,
        },
        elapsed_seconds: 12.5,
      };

      render(<ProgressDashboard status={status} />);

      // Badges in header
      expect(screen.getByText('6 Duplicates Skipped')).toBeInTheDocument();
      expect(screen.getByText('5 Baseline Inherited')).toBeInTheDocument();

      // Metric card in grid
      expect(screen.getByText('Duplicates Skipped')).toBeInTheDocument();
      expect(screen.getByText('6')).toBeInTheDocument();
    });
  });
});
