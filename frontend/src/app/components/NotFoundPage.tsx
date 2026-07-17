import { Home, SearchX } from 'lucide-react';
import { Link } from 'react-router-dom';

import styles from './AppStatusPage.module.css';

export default function NotFoundPage() {
  return (
    <main className={styles.page}>
      <div className={styles.content}>
        <div className={styles.icon} aria-hidden="true">
          <SearchX size={28} />
        </div>
        <p className={styles.code}>404</p>
        <h1 className={styles.title}>页面不存在</h1>
        <p className={styles.message}>这个地址可能已经失效，或页面已被移动。</p>
        <div className={styles.actions}>
          <Link className={styles.action} to="/">
            <Home size={17} />
            返回首页
          </Link>
        </div>
      </div>
    </main>
  );
}
