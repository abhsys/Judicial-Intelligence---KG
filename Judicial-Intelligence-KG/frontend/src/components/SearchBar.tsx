// frontend/src/components/SearchBar.tsx
import React, { FormEvent, useState } from "react";

type SearchBarProps = {
  initialValue?: string;
  placeholder?: string;
  loading?: boolean;
  onSearch: (value: string) => void;
  onClear?: () => void;
};

export default function SearchBar({
  initialValue = "",
  placeholder = "Search by case, court, or party",
  loading = false,
  onSearch,
  onClear,
}: SearchBarProps) {
  const [value, setValue] = useState(initialValue);

  const handleSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    onSearch(value.trim());
  };

  const handleClear = () => {
    setValue("");
    onSearch("");
    onClear?.();
  };

  return (
    <form className="search-row" onSubmit={handleSubmit} role="search" aria-label="Case search">
      <input
        type="search"
        value={value}
        placeholder={placeholder}
        onChange={(e) => setValue(e.target.value)}
        aria-label="Search input"
      />
      <button type="submit" disabled={loading}>
        {loading ? "Searching..." : "Search"}
      </button>
      <button type="button" onClick={handleClear} disabled={loading || value.length === 0}>
        Clear
      </button>
    </form>
  );
}
