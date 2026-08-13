(function () {
  'use strict';

  function initValidationPage() {
    const form = document.getElementById('validationForm');
    if (!form) return;

    const isService = form.dataset.anomalyLevel === 'SERVICE';
    const checkboxes = Array.from(document.querySelectorAll('input[name="predictions"]'));
    const legacyPrediction = document.getElementById('id_prediction');
    const motifSelect = document.getElementById('id_motif_final');
    const systemSelect = document.getElementById('id_systeme_a_corriger_final');
    const decisionInputs = Array.from(document.querySelectorAll('input[name="decision"]'));
    const causesPanel = document.getElementById('causesPanel');
    const selectAllButton = document.getElementById('selectAllCauses');
    const clearButton = document.getElementById('clearCauses');
    const adjustmentPanel = document.getElementById('adjustmentPanel');
    const motifField = adjustmentPanel ? adjustmentPanel.querySelector('.validation-motif-field') : null;
    const newReason = adjustmentPanel ? adjustmentPanel.querySelector('.validation-new-reason') : null;
    const decisionHelp = document.getElementById('decisionHelp');
    const selectionSummary = document.getElementById('selectionSummary');
    const countText = document.getElementById('selectedCountText');
    const targetsOutput = document.getElementById('selectedTargets');
    const footerSummary = document.getElementById('footerSummary');
    const adjustmentTitle = document.getElementById('adjustmentTitle');
    const adjustmentHint = document.getElementById('adjustmentHint');
    const submitButton = document.getElementById('submitDecision');
    const submitLabel = document.getElementById('submitDecisionLabel');

    if (!adjustmentPanel || !submitButton || !submitLabel) return;
    if (!isService && (!selectionSummary || !countText || !targetsOutput)) return;

    const metadata = {};
    document.querySelectorAll('#predictionMetadata [data-prediction-id]').forEach(function (node) {
      metadata[node.dataset.predictionId] = {
        motifId: node.dataset.motifId,
        systemId: node.dataset.systemId,
        targets: (node.dataset.targets || '').split(',').map(function (value) {
          return value.trim();
        }).filter(Boolean)
      };
    });

    function firstMetadata() {
      const id = Object.keys(metadata)[0];
      return id ? {id: id, item: metadata[id]} : null;
    }

    function selectedDecision() {
      const checked = document.querySelector('input[name="decision"]:checked');
      return checked ? checked.value : 'ACCEPTE';
    }

    function selectedPredictions() {
      return checkboxes.filter(function (input) { return input.checked; });
    }

    function selectedTargets(selected) {
      const targets = [];
      selected.forEach(function (input) {
        const item = metadata[input.value];
        if (!item) return;
        item.targets.forEach(function (code) {
          if (!targets.includes(code)) targets.push(code);
        });
      });
      return targets;
    }

    function renderTargetList(targets) {
      if (!targetsOutput) return;
      targetsOutput.replaceChildren();
      if (!targets.length) {
        const empty = document.createElement('em');
        empty.textContent = 'À déterminer';
        targetsOutput.appendChild(empty);
        return;
      }
      targets.forEach(function (code) {
        const tag = document.createElement('span');
        tag.className = 'validation-target';
        tag.textContent = code;
        targetsOutput.appendChild(tag);
      });
    }

    function configureServiceConstat() {
      if (!isService) return;
      const primary = firstMetadata();
      if (!primary) return;
      if (legacyPrediction) legacyPrediction.value = primary.id;
      if (motifSelect && primary.item.motifId) motifSelect.value = primary.item.motifId;
      if (systemSelect && selectedDecision() === 'ACCEPTE' && primary.item.systemId) {
        systemSelect.value = primary.item.systemId;
      }
    }

    function renderSelection() {
      if (isService) {
        configureServiceConstat();
        submitButton.disabled = false;
        if (footerSummary && selectedDecision() === 'ACCEPTE') {
          const copy = footerSummary.querySelector('span');
          if (copy) copy.textContent = 'Le constat sera enregistré et le système concerné sera notifié.';
        }
        return;
      }

      const selected = selectedPredictions();
      const targets = selectedTargets(selected);
      const count = selected.length;
      countText.textContent = count + ' cause' + (count > 1 ? 's' : '') + ' sélectionnée' + (count > 1 ? 's' : '');
      renderTargetList(targets);
      selectionSummary.hidden = selectedDecision() !== 'ACCEPTE';

      const primary = count ? metadata[selected[0].value] : null;
      if (legacyPrediction) legacyPrediction.value = count ? selected[0].value : '';
      if (selectedDecision() === 'ACCEPTE' && primary) {
        if (motifSelect && primary.motifId) motifSelect.value = primary.motifId;
        if (systemSelect && primary.systemId) systemSelect.value = primary.systemId;
      }
      if (selectedDecision() === 'ACCEPTE') submitButton.disabled = count === 0;

      if (footerSummary && selectedDecision() === 'ACCEPTE') {
        const copy = footerSummary.querySelector('span');
        if (copy) {
          copy.textContent = count
            ? count + ' cause' + (count > 1 ? 's' : '') + (targets.length ? ' · ' + targets.join(', ') : '')
            : 'Sélectionnez au moins une cause.';
        }
      }
    }

    function setCauseSelection(enabled) {
      if (isService) return;
      checkboxes.forEach(function (input) { input.disabled = !enabled; });
      if (selectAllButton) selectAllButton.disabled = !enabled;
      if (clearButton) clearButton.disabled = !enabled;
      if (causesPanel) causesPanel.classList.toggle('is-inactive', !enabled);
    }

    function renderDecision() {
      const decision = selectedDecision();
      const isAccept = decision === 'ACCEPTE';
      const isModify = decision === 'MODIFIE';
      const isUnknown = decision === 'INCONNU';

      setCauseSelection(isAccept);
      adjustmentPanel.hidden = isAccept;
      if (motifField) motifField.hidden = isService || !isModify;
      if (newReason) newReason.hidden = isService || !isModify;

      if (isAccept) {
        if (decisionHelp) {
          decisionHelp.textContent = isService
            ? 'Confirmez le système où le service est absent.'
            : 'Confirmez simplement les causes cochées.';
        }
        submitLabel.textContent = isService ? 'Confirmer et notifier' : 'Valider et notifier';
      } else if (isModify) {
        if (decisionHelp) {
          decisionHelp.textContent = isService
            ? 'Choisissez le système réellement concerné par la désynchronisation.'
            : 'Le diagnostic proposé sera remplacé par votre choix.';
        }
        if (adjustmentTitle) adjustmentTitle.textContent = isService ? 'Ajuster le système concerné' : 'Ajuster le diagnostic';
        if (adjustmentHint) adjustmentHint.textContent = isService
          ? 'Aucun motif métier n’est demandé à ce niveau.'
          : 'Choisissez le système responsable et la cause retenue.';
        submitLabel.textContent = 'Enregistrer et notifier';
        submitButton.disabled = false;
        if (footerSummary) {
          const copy = footerSummary.querySelector('span');
          if (copy) copy.textContent = isService
            ? 'Le constat sera enregistré avec le système choisi.'
            : 'Le diagnostic ajusté sera enregistré avant notification.';
        }
      } else if (isUnknown) {
        if (decisionHelp) decisionHelp.textContent = 'Le système concerné doit approfondir l’analyse.';
        if (adjustmentTitle) adjustmentTitle.textContent = 'Poursuivre l’investigation';
        if (adjustmentHint) adjustmentHint.textContent = 'Choisissez le système qui doit approfondir l’analyse.';
        submitLabel.textContent = 'Envoyer pour investigation';
        submitButton.disabled = false;
        if (footerSummary) {
          const copy = footerSummary.querySelector('span');
          if (copy) copy.textContent = 'Le système choisi sera sollicité pour investigation.';
        }
      }

      renderSelection();
    }

    checkboxes.forEach(function (input) { input.addEventListener('change', renderSelection); });
    decisionInputs.forEach(function (input) { input.addEventListener('change', renderDecision); });
    if (selectAllButton) {
      selectAllButton.addEventListener('click', function () {
        checkboxes.forEach(function (input) { input.checked = true; });
        renderSelection();
      });
    }
    if (clearButton) {
      clearButton.addEventListener('click', function () {
        checkboxes.forEach(function (input) { input.checked = false; });
        renderSelection();
      });
    }

    renderDecision();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initValidationPage, {once: true});
  } else {
    initValidationPage();
  }
})();
