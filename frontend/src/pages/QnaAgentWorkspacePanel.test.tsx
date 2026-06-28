import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { AUTH_USER_STORAGE_KEY } from '../api/request';
import '../test/setup';
import QnaAgentWorkspacePanel from './QnaAgentWorkspacePanel';
import { recordConversationResourceEvent } from './resourceGenerationStore';

const llmProvenance = {
  generatedBy: 'LLM',
  contentOrigin: 'LLM',
  provider: 'unit-provider',
  model: 'unit-model',
  agentName: 'document_generator',
  evidenceIds: ['doc-1'],
  fallback: false,
  fromCache: false,
};

function seedDocumentResource() {
  recordConversationResourceEvent('conversation-1', 'resource_file', {
    event: 'resource_file',
    seq: 1,
    payload: {
      ...llmProvenance,
      taskId: 'task-1',
      assetType: 'DOCUMENT',
      title: '联合索引讲义',
      summary: '已生成讲义',
      displayMode: 'MARKDOWN_CARD',
      fileName: 'guide.md',
      downloadUrl: '/api/assets/download/token',
    },
  });
}

describe('QnaAgentWorkspacePanel', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    window.localStorage.setItem(AUTH_USER_STORAGE_KEY, JSON.stringify({ id: 'user-1' }));
  });

  afterEach(() => {
    cleanup();
  });

  it('shows resources from the active conversation session', () => {
    seedDocumentResource();

    render(
      <MemoryRouter>
        <QnaAgentWorkspacePanel conversationId="conversation-1" hasStartedConversation />
      </MemoryRouter>,
    );

    expect(screen.getByRole('complementary', { name: '本轮产物' })).toBeInTheDocument();
    expect(screen.getByText('联合索引讲义')).toBeInTheDocument();
    expect(screen.getByText('已生成讲义')).toBeInTheDocument();
  });

  it('stays hidden before a conversation starts', () => {
    render(
      <MemoryRouter>
        <QnaAgentWorkspacePanel conversationId="conversation-1" hasStartedConversation={false} />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('complementary', { name: '本轮产物' })).not.toBeInTheDocument();
  });

  it('collapses the workspace body when the collapse button is clicked', async () => {
    seedDocumentResource();
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <QnaAgentWorkspacePanel conversationId="conversation-1" hasStartedConversation />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole('button', { name: '收起本轮产物' }));

    expect(screen.getByRole('complementary', { name: '本轮产物' })).toHaveAttribute('data-state', 'collapsed');
    expect(screen.queryByText('联合索引讲义')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '展开本轮产物' })).toBeInTheDocument();
  });
});
