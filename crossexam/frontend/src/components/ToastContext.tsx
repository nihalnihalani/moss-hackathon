/* eslint-disable react-refresh/only-export-components */
import React, { createContext, useContext, useState, useCallback, useMemo } from 'react';

export type ToastType = 'success' | 'error' | 'info' | 'warning';

export interface Toast {
  id: string;
  message: string;
  type: ToastType;
  duration?: number;
}

interface ToastContextType {
  toasts: Toast[];
  addToast: (message: string, type?: ToastType, duration?: number) => void;
  removeToast: (id: string) => void;
  success: (message: string, duration?: number) => void;
  error: (message: string, duration?: number) => void;
  info: (message: string, duration?: number) => void;
}

const ToastContext = createContext<ToastContextType | undefined>(undefined);

export function ToastProvider({ children }: { children: React.ReactNode }): JSX.Element {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const addToast = useCallback(
    (message: string, type: ToastType = 'info', duration = 5000) => {
      const id = Math.random().toString(36).substring(2, 9);
      setToasts((prev) => [...prev, { id, message, type, duration }]);

      if (duration > 0) {
        setTimeout(() => {
          removeToast(id);
        }, duration);
      }
    },
    [removeToast],
  );

  const success = useCallback((message: string, duration?: number) => addToast(message, 'success', duration), [addToast]);
  const error = useCallback((message: string, duration?: number) => addToast(message, 'error', duration), [addToast]);
  const info = useCallback((message: string, duration?: number) => addToast(message, 'info', duration), [addToast]);

  const value = useMemo(
    () => ({ toasts, addToast, removeToast, success, error, info }),
    [toasts, addToast, removeToast, success, error, info],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastContainer toasts={toasts} removeToast={removeToast} />
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextType {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within a ToastProvider');
  }
  return context;
}

function ToastContainer({ toasts, removeToast }: { toasts: Toast[]; removeToast: (id: string) => void }) {
  return (
    <div className="toast-container" aria-live="polite" aria-atomic="true">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast toast--${toast.type}`} role="alert">
          <div className="toast__message">{toast.message}</div>
          <button
            className="toast__close"
            onClick={() => removeToast(toast.id)}
            aria-label="Close notification"
          >
            &times;
          </button>
        </div>
      ))}
    </div>
  );
}
