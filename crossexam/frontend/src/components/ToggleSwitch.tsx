import { forwardRef } from 'react';

export interface ToggleSwitchProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label: string;
}

export const ToggleSwitch = forwardRef<HTMLInputElement, ToggleSwitchProps>(
  ({ label, checked, onChange, ...props }, ref) => {
    return (
      <label className="toggle-switch-wrapper">
        <div className={`toggle-switch ${checked ? 'toggle-switch--active' : ''}`}>
          <input
            type="checkbox"
            className="toggle-switch__input"
            checked={checked}
            onChange={onChange}
            ref={ref}
            {...props}
          />
          <div className="toggle-switch__knob" />
        </div>
        <span className="toggle-switch__label">{label}</span>
      </label>
    );
  }
);
ToggleSwitch.displayName = 'ToggleSwitch';
