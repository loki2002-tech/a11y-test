import React, { useState } from "react";

interface RegisterProps {
  onSuccess?: () => void;
}

/**
 * Registration form component.
 *
 * KNOWN ACCESSIBILITY BUGS (for test fixture purposes):
 *   Line 36: Button has no accessible name (WCAG 4.1.2)
 *   Line 44: Input has no associated label (WCAG 1.3.1)
 */
const Register: React.FC<RegisterProps> = ({ onSuccess }) => {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const handleRegister = async () => {
    setLoading(true);
    try {
      // Registration logic
      onSuccess?.();
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="registration-container">
      <h1>Create your account</h1>

      <form onSubmit={(e) => { e.preventDefault(); handleRegister(); }}>

        {/* BUG: Button missing aria-label — no visible text, no accessible name */}
        <button
          className="register-btn"
          type="submit"
          onClick={handleRegister}
          disabled={loading}
        >
          {loading ? <span className="spinner" /> : <img src="/icons/check.svg" />}
        </button>

        {/* BUG: Input has no <label> and no aria-label / aria-labelledby */}
        <input
          id="email-input"
          className="form-input form-input--email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Email address"
        />

        <input
          id="password-input"
          className="form-input form-input--password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
        />

      </form>
    </div>
  );
};

export default Register;
