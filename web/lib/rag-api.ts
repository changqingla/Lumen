import { API_BASE_URL } from './api';

interface ChatRequest {
  kb_id?: string;
  doc_ids?: string[];
  message: string;
  session_id: string;
  mode: 'deep' | 'search';
  enable_web_search?: boolean;
  show_thinking?: boolean;  // 是否显示思考过程（深度思考模式）
}

interface StreamChatOptions extends ChatRequest {
  onToken: (token: string) => void;
  onThinking: (thinking: string) => void;
  onError: (error: string) => void;
  onDone: () => void;
  onFinalAnswer?: (data: {
    answer: string;
    session_id: string;
  }) => void;
  // 中止信号
  signal?: AbortSignal;
}

class RAGAPIClient {
  private baseURL: string;

  constructor() {
    this.baseURL = API_BASE_URL;
  }

  /**
   * Stream chat with RAG service
   */
  async streamChat(options: StreamChatOptions): Promise<void> {
    const { 
      onToken, 
      onThinking, 
      onError, 
      onDone, 
      onFinalAnswer,
      signal,
      ...request 
    } = options;

    try {
      const token = localStorage.getItem('auth_token');
      const response = await fetch(`${this.baseURL}/rag/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(request),
        signal // 传递中止信号
      });

      if (!response.ok) {
        // 处理401 Unauthorized错误（Token过期）
        if (response.status === 401) {
          localStorage.removeItem('auth_token');
          localStorage.removeItem('userProfile');
          setTimeout(() => {
            window.location.href = '/auth';
          }, 1500);
          throw new Error('当前登录已过期，请重新登录');
        }
        
        // 处理429 配额超限错误
        if (response.status === 429) {
          try {
            const errorData = await response.json();
            const error = errorData.detail?.error || errorData.error || errorData;
            if (error.code === 'QUOTA_EXCEEDED') {
              // 抛出包含详细信息的错误
              const quotaError = new Error(error.message || '配额已用尽');
              (quotaError as any).code = 'QUOTA_EXCEEDED';
              (quotaError as any).details = error.details;
              throw quotaError;
            }
          } catch (e) {
            if ((e as any).code === 'QUOTA_EXCEEDED') throw e;
          }
          throw new Error('请求过于频繁，请稍后再试');
        }
        
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      if (!response.body) {
        throw new Error('Response body is null');
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.trim() || !line.startsWith('data: ')) continue;
          
          const data = line.slice(6); // Remove "data: " prefix
          
          if (data === '[DONE]') {
            onDone();
            return;
          }

          try {
            const chunk = JSON.parse(data);

            switch (chunk.type) {
              case 'token':
                onToken(chunk.content);
                break;
              case 'thinking':
                onThinking(chunk.content);
                break;
              case 'final_answer':
                if (onFinalAnswer) {
                  const faData = JSON.parse(chunk.content);
                  onFinalAnswer(faData);
                }
                break;
              case 'error':
                // 尝试解析 JSON 格式的错误信息
                try {
                  const errorData = JSON.parse(chunk.content);
                  // 如果是结构化错误（配额超限等），传递完整对象
                  if (errorData.code && errorData.details) {
                    onError(errorData as any);
                  } else {
                    // 否则传递原始字符串
                    onError(chunk.content);
                  }
                } catch {
                  // 解析失败，传递原始字符串
                  onError(chunk.content);
                }
                return;
              default:
                console.warn(`Unknown SSE event type: ${chunk.type}`, chunk);
                break;
            }
          } catch (e) {
            console.warn('Failed to parse chunk:', data, e);
          }
        }
      }

      onDone();
    } catch (error) {
      // 如果是用户主动中止，不触发错误回调
      if (error instanceof DOMException && error.name === 'AbortError') {
        onDone();
        return;
      }
      onError(String(error));
    }
  }

  /**
   * Non-streaming chat (for testing)
   */
  async chat(request: ChatRequest): Promise<any> {
    const token = localStorage.getItem('auth_token');
    const response = await fetch(`${this.baseURL}/rag/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify(request)
    });

    if (!response.ok) {
      // 处理401 Unauthorized错误（Token过期）
      if (response.status === 401) {
        localStorage.removeItem('auth_token');
        localStorage.removeItem('userProfile');
        setTimeout(() => {
          window.location.href = '/auth';
        }, 1500);
        throw new Error('当前登录已过期，请重新登录');
      }
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * Cancel an ongoing stream for a session
   */
  async cancelStream(sessionId: string): Promise<{ success: boolean; message?: string; error?: string }> {
    try {
      const token = localStorage.getItem('auth_token');
      const response = await fetch(`${this.baseURL}/rag/chat/cancel/${sessionId}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        }
      });

      if (!response.ok) {
        // 处理401 Unauthorized错误（Token过期）
        if (response.status === 401) {
          localStorage.removeItem('auth_token');
          localStorage.removeItem('userProfile');
          setTimeout(() => {
            window.location.href = '/auth';
          }, 1500);
          throw new Error('当前登录已过期，请重新登录');
        }
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      return response.json();
    } catch (error) {
      console.error('Failed to cancel stream:', error);
      return { success: false, error: String(error) };
    }
  }
}

export const ragAPI = new RAGAPIClient();
