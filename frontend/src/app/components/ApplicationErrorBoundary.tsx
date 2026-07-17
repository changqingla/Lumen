import { Component, type ErrorInfo, type ReactNode } from 'react';
import { AlertCircle, Home, RefreshCw, RotateCcw } from 'lucide-react';

import {
  classifyApplicationError,
  getApplicationErrorCopy,
  shouldResetApplicationError,
} from '@/app/lib/error-recovery';
import styles from './AppStatusPage.module.css';

interface ApplicationErrorBoundaryProps {
  children: ReactNode;
  resetKey: string;
}

interface ApplicationErrorBoundaryState {
  error: unknown;
  hasError: boolean;
}

export default class ApplicationErrorBoundary extends Component<
  ApplicationErrorBoundaryProps,
  ApplicationErrorBoundaryState
> {
  state: ApplicationErrorBoundaryState = { error: null, hasError: false };

  static getDerivedStateFromError(error: unknown): ApplicationErrorBoundaryState {
    return { error, hasError: true };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error(
      'Unhandled application render failure',
      {
        errorKind: classifyApplicationError(error),
        componentStack: info.componentStack || undefined,
      },
    );
  }

  componentDidUpdate(previousProps: ApplicationErrorBoundaryProps) {
    if (
      this.state.hasError
      && shouldResetApplicationError(previousProps.resetKey, this.props.resetKey)
    ) {
      this.setState({ error: null, hasError: false });
    }
  }

  private retry = () => {
    this.setState({ error: null, hasError: false });
  };

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }

    const copy = getApplicationErrorCopy(this.state.error);
    return (
      <main className={styles.page} role="alert">
        <div className={styles.content}>
          <div className={styles.icon} aria-hidden="true">
            <AlertCircle size={28} />
          </div>
          <p className={styles.code}>APPLICATION ERROR</p>
          <h1 className={styles.title}>{copy.title}</h1>
          <p className={styles.message}>{copy.message}</p>
          <div className={styles.actions}>
            <button type="button" className={styles.action} onClick={this.retry}>
              <RotateCcw size={17} />
              重试
            </button>
            <button
              type="button"
              className={styles.secondaryAction}
              onClick={() => window.location.reload()}
            >
              <RefreshCw size={17} />
              刷新页面
            </button>
            <button
              type="button"
              className={styles.secondaryAction}
              onClick={() => window.location.assign('/')}
            >
              <Home size={17} />
              返回首页
            </button>
          </div>
        </div>
      </main>
    );
  }
}
