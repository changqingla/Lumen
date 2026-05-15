import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import heroImage1 from '@/assets/show/image-a40e9b1a22ad.webp';
import heroImage2 from '@/assets/show/image-c5c42bc275b8.webp';
import heroImage3 from '@/assets/show/image-9414bca96a27.webp';
import heroImage4 from '@/assets/show/image-758c70f191b1.webp';
import heroImage5 from '@/assets/show/image-06e1d61b8d42.webp';
import { authAPI } from '@/shared/api/client';
import { dispatchAuthSessionReset } from '@/shared/lib/auth-runtime';
import { disableGuestMode, enableGuestMode } from '@/shared/lib/guest-mode';
import { copyTextToClipboard } from '@/shared/utils/clipboard';
import { getErrorMessage } from '@/shared/utils/errorMessage';
import {
  ArrowRight,
  BarChart3,
  Brain,
  ChevronLeft,
  ChevronRight,
  Copy,
  Cpu,
  Database,
  FileText,
  GitBranch,
  Globe,
  Layers,
  Lock,
  Mail,
  MessageSquare,
  Loader2,
  Share2,
  ShieldCheck,
  Terminal,
  User,
  Users,
  Wand2,
  X,
} from 'lucide-react';

const heroImages = [
  heroImage1,
  heroImage2,
  heroImage3,
  heroImage4,
  heroImage5,
];

const preloadHomePage = () => import('@/features/chat/pages/HomePage');

const featureItems = [
  {
    icon: MessageSquare,
    title: 'Contextual Chat',
    description: 'Multi-modal interaction with instant access to your entire enterprise knowledge base.',
    color: 'text-secondary',
  },
  {
    icon: GitBranch,
    title: 'RAG Pipeline',
    description: 'Automated ingestion, chunking, and embedding with state-of-the-art vector retrieval.',
    color: 'text-primary',
  },
  {
    icon: Cpu,
    title: 'Agent Runtime',
    description: 'Built-in support for LangGraph agents and secure gateway execution environments.',
    color: 'text-tertiary',
  },
  {
    icon: BarChart3,
    title: 'Knowledge Ops',
    description: 'Real-time monitoring of data flows and performance metrics for your AI assets.',
    color: 'text-secondary',
  },
  {
    icon: FileText,
    title: 'Notes & Memory',
    description: 'Collaborative workspace for long-form reasoning and persistent agent memory.',
    color: 'text-primary',
  },
  {
    icon: ShieldCheck,
    title: 'Team & Admin',
    description: 'Enterprise-grade RBAC, workspace silos, and comprehensive audit logging.',
    color: 'text-tertiary',
  },
];

function Header({ onLoginClick }: { onLoginClick: () => void }) {
  return (
    <header className="sticky top-0 z-50 w-full bg-[#0b1326]/70 backdrop-blur-3xl shadow-[0_8px_32px_0_rgba(218,226,253,0.1)]">
      <nav className="mx-auto flex max-w-7xl items-center justify-between px-8 py-4 font-headline tracking-tight">
        <motion.div initial={{ opacity: 0, x: -20 }} animate={{ opacity: 1, x: 0 }} className="text-2xl font-bold tracking-tighter text-primary">
          Ethereal Engine
        </motion.div>

        <div className="hidden items-center gap-8 md:flex">
          <a className="border-b-2 border-primary pb-1 text-primary transition-all duration-200 ease-in-out" href="#features">
            Features
          </a>
          <a className="text-on-surface/70 transition-all duration-200 ease-in-out hover:text-primary" href="#architecture">
            Architecture
          </a>
          <a className="text-on-surface/70 transition-all duration-200 ease-in-out hover:text-primary" href="#quick-start">
            Docs
          </a>
        </div>

        <motion.div initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} className="flex items-center gap-4">
          <button
            onClick={onLoginClick}
            className="rounded-lg bg-primary/10 px-5 py-2 font-medium text-primary transition-all duration-200 hover:bg-primary/20"
          >
            Login in
          </button>
        </motion.div>
      </nav>
    </header>
  );
}

function Hero({ onTryDemo }: { onTryDemo: () => void }) {
  const [currentIndex, setCurrentIndex] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setCurrentIndex((prev) => (prev + 1) % heroImages.length);
    }, 5000);

    return () => window.clearInterval(timer);
  }, []);

  return (
    <section className="relative overflow-hidden pb-32 pt-24">
      <div className="pointer-events-none absolute inset-0 z-0 overflow-hidden">
        <div className="absolute -left-[10%] -top-[20%] h-[60%] w-[60%] rounded-full bg-primary/10 blur-[120px]" />
        <div className="absolute -right-[5%] top-[10%] h-[50%] w-[40%] rounded-full bg-secondary/10 blur-[120px]" />
      </div>

      <div className="relative z-10 mx-auto max-w-7xl px-8 text-center">
        <motion.h1
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.1 }}
          className="text-glow mb-8 font-headline text-6xl font-bold leading-[0.9] tracking-tighter text-on-surface md:text-8xl"
        >
          Lumen: The All-in-One <br className="hidden md:block" /> AI Workspace.
        </motion.h1>

        <motion.p
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.2 }}
          className="mx-auto mb-12 max-w-3xl text-xl font-light leading-relaxed text-on-surface-variant md:text-2xl"
        >
          More than a chat demo, it&apos;s a production-grade system for teams to manage Documents, Knowledge, and Agent
          Workflows in a unified ethereal environment.
        </motion.p>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.3 }}
          className="flex flex-col justify-center gap-6 md:flex-row"
        >
          <a
            className="hero-gradient flex items-center justify-center gap-2 rounded-xl px-8 py-4 text-lg font-semibold text-white shadow-lg shadow-primary-container/20 transition-transform hover:scale-[1.02]"
            href="https://github.com/changqingla/Lumen"
            target="_blank"
            rel="noreferrer"
          >
            Get Started on GitHub
            <ArrowRight className="h-5 w-5" />
          </a>
          <button
            className="flex items-center justify-center gap-2 rounded-xl border border-on-surface/20 bg-surface-container-low/50 px-8 py-4 text-lg font-semibold text-on-surface backdrop-blur-md transition-all hover:bg-surface-container-high"
            onClick={onTryDemo}
          >
            Try Demo
          </button>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.8, delay: 0.5 }}
          className="group relative mt-24 aspect-video overflow-hidden rounded-2xl border border-outline-variant/10 shadow-2xl"
        >
          <AnimatePresence mode="wait">
            <motion.img
              key={currentIndex}
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              transition={{ duration: 0.5 }}
              alt={`Lumen Workspace Dashboard ${currentIndex + 1}`}
              className="h-full w-full object-cover"
              src={heroImages[currentIndex]}
              referrerPolicy="no-referrer"
            />
          </AnimatePresence>

          <button
            onClick={() => setCurrentIndex((prev) => (prev - 1 + heroImages.length) % heroImages.length)}
            className="absolute left-4 top-1/2 z-20 -translate-y-1/2 rounded-full bg-black/20 p-2 text-white opacity-0 backdrop-blur-sm transition-opacity group-hover:opacity-100 hover:bg-black/40"
          >
            <ChevronLeft className="h-6 w-6" />
          </button>
          <button
            onClick={() => setCurrentIndex((prev) => (prev + 1) % heroImages.length)}
            className="absolute right-4 top-1/2 z-20 -translate-y-1/2 rounded-full bg-black/20 p-2 text-white opacity-0 backdrop-blur-sm transition-opacity group-hover:opacity-100 hover:bg-black/40"
          >
            <ChevronRight className="h-6 w-6" />
          </button>

          <div className="absolute bottom-6 left-1/2 z-20 flex -translate-x-1/2 gap-2">
            {heroImages.map((_, index) => (
              <button
                key={index}
                onClick={() => setCurrentIndex(index)}
                className={`h-2 rounded-full transition-all ${index === currentIndex ? 'w-6 bg-primary' : 'w-2 bg-white/50'}`}
              />
            ))}
          </div>

          <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-background via-transparent to-transparent" />
        </motion.div>
      </div>
    </section>
  );
}

function ValueProp() {
  return (
    <section className="bg-surface py-32">
      <div className="mx-auto max-w-7xl px-8">
        <div className="grid items-center gap-24 md:grid-cols-2">
          <motion.div
            initial={{ opacity: 0, x: -30 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.6 }}
          >
            <h2 className="mb-8 font-headline text-4xl font-bold leading-tight text-on-surface md:text-5xl">
              Why Lumen? <br />
              <span className="text-secondary">Stop Fragmenting Your AI.</span>
            </h2>
            <p className="mb-12 text-lg leading-relaxed text-on-surface-variant">
              Traditional tools force you to jump between chat interfaces, RAG databases, and agent orchestration layers.
              Lumen unifies the entire lifecycle of knowledge into a single, cohesive engine.
            </p>

            <div className="space-y-6">
              <div className="flex items-start gap-4">
                <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-primary-container/20 text-primary">
                  <Database className="h-6 w-6" />
                </div>
                <div>
                  <h4 className="mb-1 font-headline text-xl font-bold">Unified Data Stream</h4>
                  <p className="text-on-surface-variant/80">Connect documentation, databases, and real-time APIs without manual ETL.</p>
                </div>
              </div>
              <div className="flex items-start gap-4">
                <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-secondary-container/20 text-secondary">
                  <Brain className="h-6 w-6" />
                </div>
                <div>
                  <h4 className="mb-1 font-headline text-xl font-bold">Contextual Memory</h4>
                  <p className="text-on-surface-variant/80">Every interaction builds a team-wide knowledge base that persists and evolves.</p>
                </div>
              </div>
            </div>
          </motion.div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-4 pt-12">
              <motion.div
                whileHover={{ y: -5 }}
                className="flex aspect-square flex-col justify-between rounded-3xl border border-outline-variant/10 bg-surface-container-low p-6"
              >
                <Layers className="h-10 w-10 text-tertiary" />
                <span className="font-headline text-lg font-bold">Integrated RAG Pipeline</span>
              </motion.div>
              <motion.div
                whileHover={{ y: -5 }}
                className="aspect-video rounded-3xl border border-outline-variant/10 bg-surface-container-highest p-6"
              >
                <div className="mb-4 flex h-8 w-8 items-center justify-center rounded-full bg-primary/20">
                  <Terminal className="h-4 w-4 text-primary" />
                </div>
                <span className="mb-1 block text-[10px] uppercase tracking-widest opacity-50">Dev Tools</span>
                <span className="font-headline font-bold">API Gateway</span>
              </motion.div>
            </div>
            <div className="space-y-4">
              <motion.div
                whileHover={{ y: -5 }}
                className="aspect-video rounded-3xl border border-outline-variant/10 bg-surface-container-high p-6"
              >
                <div className="mb-4 flex h-8 w-8 items-center justify-center rounded-full bg-secondary/20">
                  <Users className="h-4 w-4 text-secondary" />
                </div>
                <span className="mb-1 block text-[10px] uppercase tracking-widest opacity-50">Workflow</span>
                <span className="font-headline font-bold">Team Collab</span>
              </motion.div>
              <motion.div
                whileHover={{ y: -5 }}
                className="flex aspect-square flex-col justify-between rounded-3xl border border-outline-variant/10 bg-surface-container-low p-6"
              >
                <Wand2 className="h-10 w-10 text-primary" />
                <span className="font-headline text-lg font-bold">Agentic Automation</span>
              </motion.div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function Features() {
  return (
    <section id="features" className="relative py-32">
      <div className="mx-auto max-w-7xl px-8">
        <div className="mb-20 text-center">
          <h2 className="mb-4 font-headline text-4xl font-bold">Powerful Features for Modern Teams</h2>
          <p className="mx-auto max-w-2xl text-on-surface-variant">
            Architecture designed for scale, flexibility, and deep contextual awareness.
          </p>
        </div>

        <div className="grid gap-8 md:grid-cols-3">
          {featureItems.map((feature, index) => (
            <motion.div
              key={feature.title}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, delay: index * 0.1 }}
              whileHover={{ scale: 1.02 }}
              className="group rounded-3xl border border-outline-variant/5 bg-surface-container-low p-10 transition-all duration-300 hover:bg-surface-container"
            >
              <feature.icon className={`${feature.color} mb-8 h-12 w-12 transition-transform group-hover:scale-110`} />
              <h3 className="mb-4 font-headline text-2xl font-bold">{feature.title}</h3>
              <p className="leading-relaxed text-on-surface-variant">{feature.description}</p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Architecture() {
  return (
    <section id="architecture" className="bg-surface-container-lowest py-32">
      <div className="mx-auto max-w-7xl px-8">
        <div className="mb-16 text-center">
          <span className="mb-4 block text-xs font-medium uppercase tracking-[0.3em] text-secondary">System Blueprint</span>
          <h2 className="font-headline text-4xl font-bold md:text-5xl">The Ethereal Architecture</h2>
        </div>

        <motion.div
          initial={{ opacity: 0, scale: 0.98 }}
          whileInView={{ opacity: 1, scale: 1 }}
          viewport={{ once: true }}
          className="glass-panel relative overflow-hidden rounded-3xl border border-outline-variant/20 p-12"
        >
          <div className="grid gap-8">
            <div className="group flex items-center gap-12">
              <div className="w-48 text-right font-headline font-bold text-on-surface-variant">Frontend</div>
              <div className="flex h-16 flex-1 items-center justify-center rounded-xl border border-primary/20 bg-primary-container/20 text-primary transition-colors group-hover:bg-primary-container/30">
                React Dashboards &amp; Interactive Workspaces
              </div>
            </div>

            <div className="flex h-8 justify-center">
              <div className="w-px bg-gradient-to-b from-primary to-secondary" />
            </div>

            <div className="group flex items-center gap-12">
              <div className="w-48 text-right font-headline font-bold text-on-surface-variant">Backend Core</div>
              <div className="flex h-16 flex-1 items-center justify-center rounded-xl border border-secondary/20 bg-secondary-container/20 text-secondary transition-colors group-hover:bg-secondary-container/30">
                Central API Gateway (FastAPI / Node.js)
              </div>
            </div>

            <div className="flex h-8 justify-center">
              <div className="w-px bg-gradient-to-b from-secondary to-tertiary" />
            </div>

            <div className="ml-0 grid grid-cols-1 gap-8 md:ml-[240px] md:grid-cols-2">
              <div className="flex h-16 items-center justify-center rounded-xl border border-tertiary/20 bg-tertiary-container/20 text-tertiary">
                RAG Services (Vector DB)
              </div>
              <div className="flex h-16 items-center justify-center rounded-xl border border-tertiary/20 bg-tertiary-container/20 text-tertiary">
                Runtime Layer (LangGraph)
              </div>
            </div>

            <div className="flex h-8 justify-center">
              <div className="w-px bg-gradient-to-b from-tertiary to-on-surface-variant" />
            </div>

            <div className="group flex items-center gap-12">
              <div className="w-48 text-right font-headline font-bold text-on-surface-variant">Shared Infra</div>
              <div className="flex h-16 flex-1 items-center justify-center rounded-xl border border-outline-variant/30 bg-surface-container-highest/50 text-on-surface-variant transition-colors group-hover:bg-surface-container-highest">
                Docker / Redis / PostgreSQL / Object Storage
              </div>
            </div>
          </div>
        </motion.div>
      </div>
    </section>
  );
}

function QuickStart() {
  const [copied, setCopied] = useState<string | null>(null);

  const steps = [
    {
      title: '1. Clone and Navigate',
      command: 'git clone https://github.com/changqingla/Lumen.git && cd Lumen',
    },
    {
      title: '2. Configure Environment',
      command: 'cp .env.example .env # Add your API keys',
    },
    {
      title: '3. Build Project',
      command: 'npm run build',
    },
    {
      title: '4. Launch with Docker',
      command: 'docker compose up -d',
    },
  ];

  const handleCopy = async (text: string, id: string) => {
    try {
      await copyTextToClipboard(text);
      setCopied(id);
      window.setTimeout(() => setCopied(null), 2000);
    } catch {
      setCopied(null);
    }
  };

  return (
    <section id="quick-start" className="relative overflow-hidden py-32">
      <div className="relative z-10 mx-auto max-w-5xl px-8">
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="rounded-[2rem] border border-outline-variant/10 bg-surface-container p-12"
        >
          <h2 className="mb-8 flex items-center gap-4 font-headline text-3xl font-bold">
            <Terminal className="h-8 w-8 text-secondary" />
            Quick Start Guide
          </h2>

          <div className="space-y-8">
            {steps.map((step) => (
              <div key={step.title}>
                <p className="mb-4 font-medium text-on-surface-variant">{step.title}</p>
                <div className="group relative rounded-xl border border-outline-variant/20 bg-surface-container-lowest p-6 font-mono text-sm text-secondary">
                  <span className="mr-2 text-tertiary">$</span>
                  {step.command}
                  <button
                    onClick={() => handleCopy(step.command, step.title)}
                    className="absolute right-4 top-1/2 -translate-y-1/2 rounded-lg p-2 opacity-0 transition-opacity group-hover:opacity-100 hover:bg-surface-container-highest"
                  >
                    {copied === step.title ? (
                      <span className="font-sans text-xs text-secondary">Copied!</span>
                    ) : (
                      <Copy className="h-4 w-4 text-on-surface-variant" />
                    )}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </motion.div>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="w-full border-t border-on-surface/10 bg-[#060e20]">
      <div className="mx-auto max-w-7xl px-8 py-16 font-body">
        <div className="flex flex-col gap-12 border-b border-on-surface/10 pb-10 md:flex-row md:items-start md:justify-between">
          <div className="max-w-sm">
            <div className="mb-4 text-3xl font-bold tracking-tight text-primary">Lumen</div>
            <p className="leading-7 text-on-surface-variant">
              A unified AI workspace for documents, knowledge bases, and agent workflows.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-10 text-sm sm:grid-cols-3">
            <div className="space-y-4">
              <div className="text-xs uppercase tracking-[0.24em] text-on-surface-variant/60">Product</div>
              <div className="space-y-3">
                <a className="block font-medium text-on-surface-variant transition-colors hover:text-secondary" href="#features">
                  Features
                </a>
                <a className="block font-medium text-on-surface-variant transition-colors hover:text-secondary" href="#architecture">
                  Architecture
                </a>
                <a className="block font-medium text-on-surface-variant transition-colors hover:text-secondary" href="#quick-start">
                  Quick Start
                </a>
              </div>
            </div>

            <div className="space-y-4">
              <div className="text-xs uppercase tracking-[0.24em] text-on-surface-variant/60">Company</div>
              <div className="space-y-3">
                <a className="block font-medium text-on-surface-variant transition-colors hover:text-secondary" href="#">
                  About
                </a>
                <a className="block font-medium text-on-surface-variant transition-colors hover:text-secondary" href="#">
                  Blog
                </a>
                <a className="block font-medium text-on-surface-variant transition-colors hover:text-secondary" href="#">
                  Contact
                </a>
              </div>
            </div>

            <div className="space-y-4">
              <div className="text-xs uppercase tracking-[0.24em] text-on-surface-variant/60">Legal</div>
              <div className="space-y-3">
                <a className="block font-medium text-on-surface-variant transition-colors hover:text-secondary" href="#">
                  Privacy
                </a>
                <a className="block font-medium text-on-surface-variant transition-colors hover:text-secondary" href="#">
                  Security
                </a>
              </div>
            </div>
          </div>
        </div>

        <div className="flex flex-col gap-6 pt-6 text-sm text-on-surface-variant/70 md:flex-row md:items-center md:justify-between">
          <p>© 2024 Ethereal Engine Project. Built for the future of AI.</p>
          <div className="flex items-center gap-5">
            <a className="inline-flex items-center gap-2 transition-colors hover:text-secondary" href="#">
              <Globe className="h-5 w-5" />
              <span>Global</span>
            </a>
            <a className="inline-flex items-center gap-2 transition-colors hover:text-secondary" href="#">
              <Share2 className="h-5 w-5" />
              <span>Share</span>
            </a>
          </div>
        </div>
      </div>
    </footer>
  );
}

type AuthMode = 'login' | 'register' | 'reset';

const clearAuthFeedback = (
  setError: (value: string | null) => void,
  setSuccessMsg: (value: string | null) => void,
) => {
  setError(null);
  setSuccessMsg(null);
};

const mapAuthErrorMessage = (rawMessage: string, mode: AuthMode, action: 'submit' | 'send_code') => {
  const message = rawMessage.replace('Error: ', '').trim();
  const normalized = message.toLowerCase();

  if (!message) {
    return action === 'send_code'
      ? 'Unable to send the verification code right now. Please try again in a moment.'
      : 'Unable to complete your request right now. Please try again.';
  }

  if (normalized.includes('network') || normalized.includes('fetch')) {
    return 'Network connection failed. Please check your connection and try again.';
  }

  if (normalized.includes('timeout')) {
    return 'The request timed out. Please try again.';
  }

  if (normalized.includes('验证码') || normalized.includes('verification code') || normalized.includes('invalid code')) {
    return 'The verification code is invalid or expired. Please request a new one.';
  }

  if (normalized.includes('邮箱') || normalized.includes('email')) {
    if (mode === 'register' && (normalized.includes('registered') || normalized.includes('already'))) {
      return 'This email is already registered. Please sign in instead.';
    }
    if (mode === 'reset' && (normalized.includes('未注册') || normalized.includes('not found') || normalized.includes('not register'))) {
      return 'This email has not been registered yet.';
    }
    return mode === 'login'
      ? 'We could not find an account with this email address.'
      : 'Please check that your email address is correct and try again.';
  }

  if (normalized.includes('密码') || normalized.includes('password') || normalized.includes('credential')) {
    if (mode === 'login') {
      return 'Incorrect email or password.';
    }
    return 'The password does not meet the current requirements.';
  }

  if (normalized.includes('用户名') || normalized.includes('nickname') || normalized.includes('username')) {
    return 'This username is unavailable. Please choose a different one.';
  }

  if (normalized.includes('exist') || normalized.includes('already')) {
    return mode === 'register'
      ? 'This account already exists. Please sign in instead.'
      : mode === 'reset'
        ? 'This email has not been registered yet.'
        : 'This information is already in use.';
  }

  if (normalized.includes('未注册') || normalized.includes('not found') || normalized.includes('not register')) {
    return mode === 'reset'
      ? 'This email has not been registered yet.'
      : 'We could not find an account with this email address.';
  }

  if (normalized.includes('too many') || normalized.includes('频繁')) {
    return action === 'send_code'
      ? 'Too many requests. Please wait a minute before sending another code.'
      : 'Too many attempts. Please wait a moment and try again.';
  }

  if (action === 'send_code') {
    return 'Unable to send the verification code right now. Please try again later.';
  }

  if (mode === 'login') {
    return 'Unable to sign in with those credentials. Please try again.';
  }

  if (mode === 'register') {
    return 'Unable to create your account right now. Please review the form and try again.';
  }

  return 'Unable to reset your password right now. Please try again.';
};

function AuthModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const [mode, setMode] = useState<AuthMode>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [verificationCode, setVerificationCode] = useState('');
  const [loading, setLoading] = useState(false);
  const [sendingCode, setSendingCode] = useState(false);
  const [timer, setTimer] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [isClosingAfterSuccess, setIsClosingAfterSuccess] = useState(false);

  const isLogin = mode === 'login';

  useEffect(() => {
    if (isOpen) {
      void preloadHomePage();
    }
  }, [isOpen]);

  useEffect(() => {
    if (timer <= 0) {
      return undefined;
    }

    const interval = window.setInterval(() => {
      setTimer((prev) => {
        if (prev <= 1) {
          window.clearInterval(interval);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => window.clearInterval(interval);
  }, [timer]);

  const resetForm = () => {
    setMode('login');
    setEmail('');
    setPassword('');
    setName('');
    setConfirmPassword('');
    setVerificationCode('');
    setLoading(false);
    setSendingCode(false);
    setTimer(0);
    setError(null);
    setSuccessMsg(null);
    setIsClosingAfterSuccess(false);
  };

  const validateEmail = (value: string) => /.+@.+\..+/.test(value);

  const handleSendCode = async () => {
    clearAuthFeedback(setError, setSuccessMsg);

    if (!validateEmail(email)) {
      setError('Please enter a valid email address.');
      return;
    }

    setSendingCode(true);

    try {
      await authAPI.sendVerificationCode(email, mode === 'reset' ? 'reset' : 'register');
      setTimer(60);
      setSuccessMsg('Verification code sent. Please check your inbox.');
    } catch (err: unknown) {
      const msg = mapAuthErrorMessage(getErrorMessage(err, ''), mode, 'send_code');
      setError(msg);
    } finally {
      setSendingCode(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    clearAuthFeedback(setError, setSuccessMsg);

    try {
      if (!validateEmail(email)) {
        throw new Error('Please enter a valid email address.');
      }

      if (password.length < 6) {
        throw new Error('Password must be at least 6 characters.');
      }

      setLoading(true);

      if (mode === 'register') {
        if (!name.trim()) {
          throw new Error('Username is required.');
        }
        if (name.trim().length > 8) {
          throw new Error('Username must be 8 characters or fewer.');
        }
        if (!verificationCode || verificationCode.length < 6) {
          throw new Error('Please enter a valid verification code.');
        }
        if (password !== confirmPassword) {
          throw new Error('Passwords do not match.');
        }

        await authAPI.register(email, password, name.trim(), verificationCode);
        setMode('login');
        setPassword('');
        setConfirmPassword('');
        setVerificationCode('');
        setTimer(0);
        setName('');
        setSuccessMsg('Account created successfully. Please sign in.');
        return;
      }

      if (mode === 'reset') {
        if (!verificationCode || verificationCode.length < 6) {
          throw new Error('Please enter a valid verification code.');
        }
        if (password !== confirmPassword) {
          throw new Error('Passwords do not match.');
        }

        await authAPI.resetPassword(email, password, verificationCode);
        setMode('login');
        setPassword('');
        setConfirmPassword('');
        setVerificationCode('');
        setTimer(0);
        setName('');
        setSuccessMsg('Password reset successfully. Please sign in.');
        return;
      }

      const response = await authAPI.login(email, password);
      localStorage.setItem('auth_token', response.token);
      localStorage.setItem('auth_user', JSON.stringify(response.user));
      localStorage.setItem('userProfile', JSON.stringify(response.user));
      disableGuestMode();
      dispatchAuthSessionReset();
      setSuccessMsg('Signed in successfully. Redirecting to your workspace...');
      setIsClosingAfterSuccess(true);
      await preloadHomePage();
      onClose();
      navigate('/');
    } catch (err: unknown) {
      const msg = mapAuthErrorMessage(getErrorMessage(err, ''), mode, 'submit');
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!isOpen) {
      resetForm();
    }
  }, [isOpen]);

  return (
    <AnimatePresence>
      {isOpen && (
        <div className="fixed inset-0 z-[9999] overflow-y-auto">
          <div className="flex min-h-full items-center justify-center p-4 text-center">
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={onClose}
              className="fixed inset-0 bg-background/80 backdrop-blur-sm"
            />

            <motion.div
              initial={{ opacity: 0, scale: 0.95, y: 20 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 20 }}
              className="relative w-full max-w-md transform overflow-hidden rounded-3xl border border-outline-variant/20 bg-surface-container-low p-8 text-left shadow-2xl transition-all"
            >
              <button
                onClick={onClose}
                disabled={loading || isClosingAfterSuccess}
                className="absolute right-6 top-6 text-on-surface-variant transition-colors hover:text-on-surface"
              >
                <X className="h-6 w-6" />
              </button>

              <div className="mb-8 text-center">
                <h2 className="mb-2 font-headline text-3xl font-bold">
                  {mode === 'login' ? 'Welcome Back' : mode === 'register' ? 'Create Account' : 'Reset Password'}
                </h2>
                <p className="text-on-surface-variant">
                  {mode === 'login'
                    ? 'Enter your credentials to access your workspace'
                    : mode === 'register'
                      ? 'Join Lumen and start building your AI assistant'
                      : 'Set a new password using your email verification code'}
                </p>
              </div>

              <form className="space-y-4" onSubmit={handleSubmit}>
                {mode === 'register' && (
                  <div className="space-y-2">
                    <label className="ml-1 text-sm font-medium text-on-surface-variant">Username</label>
                    <div className="relative">
                      <User className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-on-surface-variant" />
                      <input
                        type="text"
                        value={name}
                        onChange={(e) => {
                          setName(e.target.value);
                          if (error) {
                            setError(null);
                          }
                        }}
                        placeholder="Your nickname"
                        className="w-full rounded-xl border border-outline-variant/20 bg-surface-container-lowest py-3 pl-12 pr-4 text-on-surface transition-all focus:outline-none focus:ring-2 focus:ring-primary/50"
                      />
                    </div>
                  </div>
                )}

                <div className="space-y-2">
                  <label className="ml-1 text-sm font-medium text-on-surface-variant">Email Address</label>
                  <div className="relative">
                    <Mail className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-on-surface-variant" />
                    <input
                      type="email"
                      value={email}
                      onChange={(e) => {
                        setEmail(e.target.value);
                        if (error) {
                          setError(null);
                        }
                      }}
                      placeholder="name@company.com"
                      className="w-full rounded-xl border border-outline-variant/20 bg-surface-container-lowest py-3 pl-12 pr-4 text-on-surface transition-all focus:outline-none focus:ring-2 focus:ring-primary/50"
                    />
                  </div>
                </div>

                {!isLogin && (
                  <div className="space-y-2">
                    <label className="ml-1 text-sm font-medium text-on-surface-variant">Verification Code</label>
                    <div className="flex gap-2">
                      <div className="relative flex-1">
                        <ShieldCheck className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-on-surface-variant" />
                        <input
                          type="text"
                          value={verificationCode}
                          onChange={(e) => {
                            setVerificationCode(e.target.value);
                            if (error) {
                              setError(null);
                            }
                          }}
                          placeholder="6-digit code"
                          className="w-full rounded-xl border border-outline-variant/20 bg-surface-container-lowest py-3 pl-12 pr-4 text-on-surface transition-all focus:outline-none focus:ring-2 focus:ring-primary/50"
                        />
                      </div>
                      <button
                        type="button"
                        onClick={handleSendCode}
                        disabled={sendingCode || timer > 0}
                        className="whitespace-nowrap rounded-xl border border-outline-variant/20 bg-surface-container-high px-4 font-medium text-on-surface transition-colors hover:bg-surface-container-highest disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {sendingCode ? 'Sending...' : timer > 0 ? `${timer}s` : 'Send Code'}
                      </button>
                    </div>
                  </div>
                )}

                <div className="space-y-2">
                  <label className="ml-1 text-sm font-medium text-on-surface-variant">Password</label>
                  <div className="relative">
                    <Lock className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-on-surface-variant" />
                    <input
                      type="password"
                      value={password}
                      onChange={(e) => {
                        setPassword(e.target.value);
                        if (error) {
                          setError(null);
                        }
                      }}
                      placeholder="••••••••"
                      className="w-full rounded-xl border border-outline-variant/20 bg-surface-container-lowest py-3 pl-12 pr-4 text-on-surface transition-all focus:outline-none focus:ring-2 focus:ring-primary/50"
                    />
                  </div>
                </div>

                {!isLogin && (
                  <div className="space-y-2">
                    <label className="ml-1 text-sm font-medium text-on-surface-variant">Confirm Password</label>
                    <div className="relative">
                      <Lock className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-on-surface-variant" />
                      <input
                        type="password"
                        value={confirmPassword}
                        onChange={(e) => {
                          setConfirmPassword(e.target.value);
                          if (error) {
                            setError(null);
                          }
                        }}
                        placeholder="••••••••"
                        className="w-full rounded-xl border border-outline-variant/20 bg-surface-container-lowest py-3 pl-12 pr-4 text-on-surface transition-all focus:outline-none focus:ring-2 focus:ring-primary/50"
                      />
                    </div>
                  </div>
                )}

                {(error || successMsg) && (
                  <div className={`rounded-xl border px-4 py-3 text-sm ${error ? 'border-red-400/30 bg-red-500/10 text-red-200' : 'border-emerald-400/30 bg-emerald-500/10 text-emerald-200'}`}>
                    {error || successMsg}
                  </div>
                )}

                <button
                  disabled={loading || isClosingAfterSuccess}
                  className="hero-gradient mt-4 flex w-full items-center justify-center gap-2 rounded-xl py-4 font-bold text-white shadow-lg shadow-primary-container/20 transition-transform hover:scale-[1.02] disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : null}
                  {mode === 'login' ? 'Sign In' : mode === 'register' ? 'Sign Up' : 'Reset Password'}
                  {!loading ? <ArrowRight className="h-5 w-5" /> : null}
                </button>
              </form>

              <div className="mt-8 space-y-3 text-center">
                <p className="text-on-surface-variant">
                  {mode === 'login' ? "Don't have an account?" : 'Already have an account?'}{' '}
                  <button
                    onClick={() => {
                      clearAuthFeedback(setError, setSuccessMsg);
                      setMode(mode === 'login' ? 'register' : 'login');
                      setPassword('');
                      setConfirmPassword('');
                      setVerificationCode('');
                    }}
                    className="font-bold text-primary hover:underline"
                  >
                    {mode === 'login' ? 'Sign Up' : 'Sign In'}
                  </button>
                </p>

                {mode === 'login' ? (
                  <button
                    onClick={() => {
                      clearAuthFeedback(setError, setSuccessMsg);
                      setMode('reset');
                      setPassword('');
                      setConfirmPassword('');
                      setVerificationCode('');
                    }}
                    className="text-sm text-on-surface-variant transition-colors hover:text-primary"
                  >
                    Forgot your password?
                  </button>
                ) : mode === 'reset' ? (
                  <button
                    onClick={() => {
                      clearAuthFeedback(setError, setSuccessMsg);
                      setMode('login');
                      setPassword('');
                      setConfirmPassword('');
                      setVerificationCode('');
                    }}
                    className="text-sm text-on-surface-variant transition-colors hover:text-primary"
                  >
                    Back to sign in
                  </button>
                ) : null}
              </div>
            </motion.div>
          </div>
        </div>
      )}
    </AnimatePresence>
  );
}

export default function AuthPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [isAuthModalOpen, setIsAuthModalOpen] = useState(false);

  useEffect(() => {
    const htmlOverflow = document.documentElement.style.overflow;
    const bodyOverflow = document.body.style.overflow;
    const root = document.getElementById('root');
    const rootOverflow = root?.style.overflow ?? '';
    const bodyBackground = document.body.style.backgroundColor;
    const rootBackground = root?.style.backgroundColor ?? '';

    document.documentElement.style.overflow = 'auto';
    document.body.style.overflow = 'auto';
    document.body.style.backgroundColor = '#0b1326';

    if (root) {
      root.style.overflow = 'visible';
      root.style.backgroundColor = '#0b1326';
    }

    return () => {
      document.documentElement.style.overflow = htmlOverflow;
      document.body.style.overflow = bodyOverflow;
      document.body.style.backgroundColor = bodyBackground;

      if (root) {
        root.style.overflow = rootOverflow;
        root.style.backgroundColor = rootBackground;
      }
    };
  }, []);

  useEffect(() => {
    const modal = searchParams.get('modal');
    if (modal !== 'login') {
      return;
    }

    setIsAuthModalOpen(true);
    setSearchParams({}, { replace: true });
  }, [searchParams, setSearchParams]);

  const handleTryDemo = () => {
    enableGuestMode();
    navigate('/');
  };

  return (
    <div className="min-h-screen overflow-y-auto bg-background text-on-surface selection:bg-primary/30 selection:text-primary">
      <Header onLoginClick={() => setIsAuthModalOpen(true)} />
      <main>
        <Hero onTryDemo={handleTryDemo} />
        <ValueProp />
        <Features />
        <Architecture />
        <QuickStart />
      </main>
      <Footer />
      <AuthModal isOpen={isAuthModalOpen} onClose={() => setIsAuthModalOpen(false)} />
    </div>
  );
}
