import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import SchemaForm from "@/components/SchemaForm";

describe("<SchemaForm />", () => {
  it("renders a text input for a string field with label and description", () => {
    const schema = {
      type: "object",
      properties: {
        title: { type: "string", description: "project title" },
      },
      required: ["title"],
    };
    const onChange = vi.fn();
    render(<SchemaForm schema={schema} value={{ title: "hello" }} onChange={onChange} />);
    expect(screen.getByText("title")).toBeInTheDocument();
    expect(screen.getByText("project title")).toBeInTheDocument();
    const input = screen.getByDisplayValue("hello") as HTMLInputElement;
    expect(input).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "new title" } });
    expect(onChange).toHaveBeenCalledWith({ title: "new title" });
  });

  it("renders a select for enum fields", () => {
    const schema = {
      type: "object",
      properties: {
        vibe: {
          type: "string",
          enum: ["emotional", "hype", "sad"],
        },
      },
    };
    const onChange = vi.fn();
    render(<SchemaForm schema={schema} value={{ vibe: "emotional" }} onChange={onChange} />);
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("emotional");
    fireEvent.change(select, { target: { value: "hype" } });
    expect(onChange).toHaveBeenCalledWith({ vibe: "hype" });
  });

  it("renders a number input with type=number", () => {
    const schema = {
      type: "object",
      properties: {
        count: { type: "integer", minimum: 0, maximum: 10 },
      },
    };
    const onChange = vi.fn();
    render(<SchemaForm schema={schema} value={{ count: 5 }} onChange={onChange} />);
    const input = screen.getByDisplayValue("5") as HTMLInputElement;
    expect(input.type).toBe("number");
    fireEvent.change(input, { target: { value: "7" } });
    expect(onChange).toHaveBeenCalledWith({ count: 7 });
  });

  it("adds and removes items from an array", () => {
    const schema = {
      type: "object",
      properties: {
        tags: {
          type: "array",
          items: { type: "string" },
        },
      },
    };
    let current: unknown = { tags: ["a", "b"] };
    const onChange = vi.fn((next: unknown) => {
      current = next;
    });
    const { rerender } = render(
      <SchemaForm schema={schema} value={current} onChange={onChange} />
    );
    const addBtn = screen.getByRole("button", { name: /add item/i });
    fireEvent.click(addBtn);
    expect((onChange.mock.calls[0]![0] as { tags: string[] }).tags).toEqual(["a", "b", ""]);

    rerender(<SchemaForm schema={schema} value={{ tags: ["a", "b", "c"] }} onChange={onChange} />);
    const removeBtns = screen.getAllByRole("button", { name: /remove item/i });
    fireEvent.click(removeBtns[1]!);
    expect((onChange.mock.calls.at(-1)![0] as { tags: string[] }).tags).toEqual(["a", "c"]);
  });

  it("handles resolved $ref definitions", () => {
    const schema = {
      type: "object",
      properties: {
        scene: { $ref: "#/$defs/Scene" },
      },
      $defs: {
        Scene: {
          type: "object",
          properties: {
            description: { type: "string" },
          },
          required: ["description"],
        },
      },
    };
    const onChange = vi.fn();
    render(
      <SchemaForm
        schema={schema}
        value={{ scene: { description: "opening" } }}
        onChange={onChange}
      />
    );
    const input = screen.getByDisplayValue("opening") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "climax" } });
    expect(onChange).toHaveBeenCalledWith({ scene: { description: "climax" } });
  });

  it("renders nested objects with section headers", () => {
    const schema = {
      type: "object",
      properties: {
        song: {
          type: "object",
          properties: {
            title: { type: "string" },
            artist: { type: "string" },
          },
        },
      },
    };
    render(
      <SchemaForm
        schema={schema}
        value={{ song: { title: "x", artist: "y" } }}
        onChange={vi.fn()}
      />
    );
    expect(screen.getByText("title")).toBeInTheDocument();
    expect(screen.getByText("artist")).toBeInTheDocument();
  });
});
