// Global Vitest setup: pull in jest-dom's custom matchers (toBeInTheDocument,
// toHaveTextContent, …) and auto-clean the DOM between tests so each component
// test mounts a fresh tree.
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});
