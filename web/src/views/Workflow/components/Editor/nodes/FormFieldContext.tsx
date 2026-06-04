import React, { createContext, useContext } from 'react';
import { type Suggestion } from '../plugin/AutocompletePlugin';

export interface FormFieldData {
  id: string;
  default_value?: string;
  variable_ref?: string;
}

interface FormFieldContextType {
  updateFormFields: (fields: FormFieldData[]) => void;
  formFields: FormFieldData[];
  options: Suggestion[];
}

export const FormFieldContext = createContext<FormFieldContextType | null>(null);

export const useFormFieldContext = (): FormFieldContextType => {
  const context = useContext(FormFieldContext);
  if (!context) {
    throw new Error('useFormFieldContext must be used within a FormFieldProvider');
  }
  return context;
};

export const FormFieldProvider: React.FC<{
  children: React.ReactNode;
  updateFormFields: (fields: FormFieldData[]) => void;
  formFields: FormFieldData[];
  options?: Suggestion[];
}> = ({ children, updateFormFields, formFields, options = [] }) => {
  return (
    <FormFieldContext.Provider value={{ updateFormFields, formFields, options }}>
      {children}
    </FormFieldContext.Provider>
  );
};
