interface PreviewBranchesProps {
  branches?: { label: string; name: string }[];
}

/** Displays branch names in the published snapshot, keeping the same order as the preview canvas ports. */
const PreviewBranches = ({ branches = [] }: PreviewBranchesProps) => (
  <div className="rb:mt-3">
    {branches.map((branch) => (
      <div
        key={branch.label}
        className="rb:h-6.5 rb:truncate rb:text-right rb:text-xs rb:text-gray-600"
        title={branch.name}
      >
        {branch.label} · {branch.name}
      </div>
    ))}
  </div>
);

export default PreviewBranches;
