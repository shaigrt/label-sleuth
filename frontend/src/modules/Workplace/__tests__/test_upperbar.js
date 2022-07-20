/*
    Copyright (c) 2022 IBM Corp.
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
*/

import React from "react";
import { screen, fireEvent } from "@testing-library/react";
import { renderWithProviderAndRouter } from "../../../utils/test-utils";
import { initialState as initialWorkspaceState } from "../DataSlice";
import UpperBar from '../upperbar/UpperBar'
import { categoriesExample } from '../../../utils/test-utils'



test("test that category action butttons are present", async () => {
    renderWithProviderAndRouter(
      <UpperBar />,
      {
        preloadedState: {
          workspace: {
            ...initialWorkspaceState,
            curCategory: categoriesExample.categories[0].category_id,
            categories: categoriesExample.categories,
          },
        },
      }
    );
  
    expect(screen.queryByRole('button', { name: /create/i})).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /delete/i})).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /edit/i})).toBeInTheDocument();
});

test("test that delete and edit category buttons are not present if no category is selected", async () => {
  renderWithProviderAndRouter(
    <UpperBar />,
    {
      preloadedState: {
        workspace: {
          ...initialWorkspaceState,
          curCategory: null,
          categories: categoriesExample.categories,
        },
      },
    }
  );

  expect(screen.queryByRole('button', { name: /delete/i})).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /edit/i})).not.toBeInTheDocument();
});



test("test create new category validation", async () => {
  renderWithProviderAndRouter(
    <UpperBar />,
    {
      preloadedState: {
        workspace: {
          ...initialWorkspaceState,
          curCategory: categoriesExample.categories[0].category_id,
          categories: categoriesExample.categories,
          workspaceId: "workspace_id"
        },
      },
    }
  );

  fireEvent.click(screen.queryByRole('button', { name: /create/i}))
  expect(screen.getByText(/Create a new category/i)).toBeInTheDocument();

  const button = screen.getByRole('button', {name: "Create"})
  const input = screen.getByRole('textbox', {name: "New category name"})
  
  expect(button).toBeDisabled()
  
  fireEvent.change(input, {target: {value: "!"}})
  expect(screen.getByText(/Name may only contain English characters, digits, underscores and spaces/i)).toBeInTheDocument()
  expect(button).toBeDisabled()
});

test("test create new category flow", async () => {
  renderWithProviderAndRouter(
    <UpperBar />,
    {
      preloadedState: {
        workspace: {
          ...initialWorkspaceState,
          curCategory: categoriesExample.categories[0].category_id,
          categories: categoriesExample.categories,
          workspaceId: "workspace_id"
        },
      },
    }
  );

  fireEvent.click(screen.queryByRole('button', { name: /create/i}))
  expect(screen.getByText(/Create a new category/i)).toBeInTheDocument();

  const button = screen.getByRole('button', {name: "Create"})
  const input = screen.getByRole('textbox', {name: "New category name"})
  
  fireEvent.change(input, {target: {value: "test_category"}})
  expect(input.value).toBe('test_category')
  expect(button).not.toBeDisabled()

  fireEvent.click(button)

  expect(await screen.findByRole("alert")).toBeInTheDocument()
  expect(await screen.findByText(/The category 'test_category' has been created/)).toBeInTheDocument()
  
  // check that modal is no longer present
  expect(screen.queryByText(/Create a new category/i)).not.toBeInTheDocument();

  // check that the created category is selected in the dropdown
  expect(screen.getByLabelText("test_category")).toBeInTheDocument()
});